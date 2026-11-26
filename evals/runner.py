"""QRClaw 记忆与 Agent 效果回归评测运行器：驱动真实业务模块并生成终端与 JSON 评测报告。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from unittest.mock import patch

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import qrclaw.agent as agent_mod
from qrclaw.graph.nodes.memory_extraction import MemoryExtractionNode
from qrclaw.graph.nodes.router import RouterNode
from qrclaw.graph.runner import GraphRunner
from qrclaw.memory.compression import compressor
from qrclaw.memory.context.runtime import manager as ctx_manager_mod
from qrclaw.memory.context.runtime.manager import init_context_manager
from qrclaw.memory.context.session import Session
from qrclaw.memory.wiki import WikiMemory
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.runner import ExtractionRunner
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema, WikiPageSchema
from qrclaw.memory.wiki.extraction.strategies import filter_memory_messages
from qrclaw.server.wiki_service import WikiService
from qrclaw.tools import filesystem
from qrclaw.tools.wiki_tools import (
    delete_wiki_page,
    read_wiki_content,
    read_wiki_page,
    reset_wiki_reads,
    search_wiki,
    write_wiki_page,
)
from qrclaw.workspace import Workspace

from evals.schemas import (
    CaseReport,
    CategoryDataset,
    CategorySummary,
    CheckItem,
    EvalCaseSpec,
    EvalMode,
    EvalReport,
)
from evals.scorers import (
    aggregate_checks,
    bool_check,
    ratio_check,
    score_keyword_groups,
    score_plan_dag,
    score_provenance_sources,
    score_summary_retention,
    score_tool_pair_integrity,
)
from evals.scripted_provider import EvalTelemetry, ensure_live_model_ready, eval_model_context

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
DATASET_ORDER = (
    "memory_extraction",
    "memory_provenance_and_retrieval",
    "context_compression",
    "agent_routing_and_tools",
)


def load_datasets(datasets_dir: Path | None = None) -> list[CategoryDataset]:
    """加载并用 Pydantic 严格校验全部四个维度的评测数据集。"""
    base_dir = datasets_dir or DATASETS_DIR
    loaded: list[CategoryDataset] = []
    seen_ids: set[str] = set()

    for name in DATASET_ORDER:
        path = base_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"缺少评测数据集文件: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        dataset = CategoryDataset.model_validate(raw)
        for case in dataset.cases:
            if case.id in seen_ids:
                raise ValueError(f"检测到重复的评测用例 ID: {case.id}")
            seen_ids.add(case.id)
        loaded.append(dataset)

    return loaded


@contextmanager
def isolated_eval_workspace(agent_id: str = "default"):
    """为单条评测用例创建完全隔离的临时 QRCLAW_HOME 与 Workspace，运行后彻底释放后台资源。"""
    prev_home = os.environ.get("QRCLAW_HOME")
    prev_auto_title = os.environ.get("QRCLAW_AUTO_TITLE")
    prev_session = agent_mod.get_session()
    prev_workspace = agent_mod.get_workspace()
    prev_extractor = agent_mod.get_extractor()
    prev_ctx = getattr(ctx_manager_mod._thread_local, "ctx", None)

    tmp_base = "/tmp" if os.path.isdir("/tmp") else None
    with tempfile.TemporaryDirectory(prefix="qrclaw-eval-", dir=tmp_base) as tmp:
        tmp_path = Path(tmp)
        home_dir = tmp_path / "home"
        home_dir.mkdir(parents=True, exist_ok=True)
        os.environ["QRCLAW_HOME"] = str(home_dir)
        os.environ["QRCLAW_AUTO_TITLE"] = "0"

        workspace = Workspace(agent_id=agent_id, _root=home_dir / "agents" / agent_id)
        wiki = WikiMemory.for_workspace(workspace.memory_dir)
        # 禁用后台自动修复定时器争用，由评测用例显式推进验证
        if wiki._repair_worker is not None:
            wiki._repair_worker.close()
        filesystem.clear_read_tracker()
        reset_wiki_reads()
        agent_mod.set_workspace(workspace)

        try:
            yield tmp_path, workspace, wiki
        finally:
            try:
                agent_mod._runner.react_loop.stop_memory_extractions(workspace)
            except Exception:
                pass
            if wiki._repair_worker is not None:
                try:
                    wiki._repair_worker.close()
                except Exception:
                    pass
            filesystem.clear_read_tracker()
            reset_wiki_reads()
            agent_mod.set_session(prev_session)
            agent_mod.set_workspace(prev_workspace)
            agent_mod.register_extractor(prev_extractor)
            ctx_manager_mod._thread_local.ctx = prev_ctx
            if prev_home is None:
                os.environ.pop("QRCLAW_HOME", None)
            else:
                os.environ["QRCLAW_HOME"] = prev_home
            if prev_auto_title is None:
                os.environ.pop("QRCLAW_AUTO_TITLE", None)
            else:
                os.environ["QRCLAW_AUTO_TITLE"] = prev_auto_title


def _fast_extraction_config(**overrides: Any) -> ExtractionConfig:
    params: dict[str, Any] = {
        "minimum_message_tokens_to_init": 1,
        "minimum_tokens_between_update": 1,
        "minimum_turns": 1,
        "idle_seconds": 0,
        "cooldown_seconds": 0,
        "max_retries": 2,
    }
    params.update(overrides)
    return ExtractionConfig(**params)


def _seed_session(workspace: Workspace, messages: list[dict], session_id: str = "eval-session") -> Session:
    session = Session(workspace.sessions_dir, session_id=session_id, resume=False)
    for msg in messages:
        session.add(dict(msg))
    session.ensure_saved()
    agent_mod.set_session(session)
    return session


class EvalRunner:
    """执行全量或筛选后的评测集，兼容 offline 与 live 双模式。"""

    def __init__(self, mode: EvalMode = "offline"):
        self.mode: EvalMode = mode
        self._dispatch = {
            # 1. 记忆提取维度（6 个场景）
            "extract_user_preferences": self._case_extract_user_preferences,
            "consolidate_override_config": self._case_consolidate_override_config,
            "skip_ephemeral_chat": self._case_skip_ephemeral_chat,
            "reject_assistant_speculation": self._case_reject_assistant_speculation,
            "filter_tool_and_summary_noise": self._case_filter_tool_and_summary_noise,
            "explicit_write_and_dedup": self._case_explicit_write_and_dedup,
            # 2. 来源追溯与检索隔离维度（5 个场景）
            "search_and_paginated_read": self._case_search_and_paginated_read,
            "invalidate_hides_page_immediately": self._case_invalidate_hides_page_immediately,
            "manual_edit_protection": self._case_manual_edit_protection,
            "tombstone_blocks_stale_restore": self._case_tombstone_blocks_stale_restore,
            "wiki_lint_health_check": self._case_wiki_lint_health_check,
            # 3. 上下文压缩维度（4 个场景）
            "recursive_compress_and_tool_pair": self._case_recursive_compress_and_tool_pair,
            "preserve_unextracted_before_compression": self._case_preserve_unextracted_before_compression,
            "summary_fact_retention_score": self._case_summary_fact_retention_score,
            "concurrent_modification_abort": self._case_concurrent_modification_abort,
            # 4. 路由决策与工具执行闭环维度（5 个场景）
            "router_direct_vs_plan_dag": self._case_router_direct_vs_plan_dag,
            "plan_disabled_skips_router": self._case_plan_disabled_skips_router,
            "react_read_patch_verify": self._case_react_read_patch_verify,
            "tool_error_self_healing": self._case_tool_error_self_healing,
            "workspace_security_boundary": self._case_workspace_security_boundary,
        }

    def run(
        self,
        datasets: list[CategoryDataset] | None = None,
        *,
        categories: set[str] | None = None,
        case_ids: set[str] | None = None,
    ) -> EvalReport:
        if self.mode == "live":
            ensure_live_model_ready()

        all_datasets = datasets or load_datasets()
        started_wall = time.perf_counter()
        case_reports: list[CaseReport] = []
        category_summaries: list[CategorySummary] = []
        detected_model = "eval/scripted-offline" if self.mode == "offline" else ""

        for dataset in all_datasets:
            if categories and dataset.category not in categories:
                continue
            selected_cases = [
                c for c in dataset.cases if not case_ids or c.id in case_ids
            ]
            if not selected_cases:
                continue

            cat_reports: list[CaseReport] = []
            for spec in selected_cases:
                report, model_used = self._execute_case(dataset.category, spec)
                if model_used:
                    detected_model = model_used
                cat_reports.append(report)
                case_reports.append(report)

            cat_weight = sum(r.weight for r in cat_reports)
            cat_score = (
                sum(r.score * r.weight for r in cat_reports) / cat_weight
                if cat_weight > 0
                else 0.0
            )
            passed_cnt = sum(1 for r in cat_reports if r.passed)
            category_summaries.append(
                CategorySummary(
                    category=dataset.category,
                    title=dataset.title,
                    total_cases=len(cat_reports),
                    passed_cases=passed_cnt,
                    pass_rate=round(passed_cnt / len(cat_reports), 4),
                    weighted_score=round(cat_score, 4),
                    duration_ms=round(sum(r.duration_ms for r in cat_reports), 2),
                    llm_calls=sum(r.llm_calls for r in cat_reports),
                    total_tokens=sum(r.total_tokens for r in cat_reports),
                )
            )

        total_cases = len(case_reports)
        passed_cases = sum(1 for r in case_reports if r.passed)
        total_weight = sum(r.weight for r in case_reports)
        overall_score = (
            sum(r.score * r.weight for r in case_reports) / total_weight
            if total_weight > 0
            else 0.0
        )
        elapsed_ms = (time.perf_counter() - started_wall) * 1000

        return EvalReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            mode=self.mode,
            model=detected_model or "unknown",
            total_cases=total_cases,
            passed_cases=passed_cases,
            pass_rate=round(passed_cases / total_cases, 4) if total_cases else 0.0,
            weighted_score=round(overall_score, 4),
            duration_ms=round(elapsed_ms, 2),
            total_llm_calls=sum(r.llm_calls for r in case_reports),
            total_prompt_tokens=sum(r.prompt_tokens for r in case_reports),
            total_completion_tokens=sum(r.completion_tokens for r in case_reports),
            total_tokens=sum(r.total_tokens for r in case_reports),
            categories=category_summaries,
            cases=case_reports,
        )

    def _execute_case(self, category: str, spec: EvalCaseSpec) -> tuple[CaseReport, str]:
        handler = self._dispatch.get(spec.scenario)
        if handler is None:
            raise ValueError(f"未知的评测场景处理器: {spec.scenario}")

        state: dict[str, Any] = {}
        t0 = time.perf_counter()
        model_name = ""
        telemetry = EvalTelemetry()

        try:
            with eval_model_context(self.mode, spec.scenario, state) as (tel, m_name):
                telemetry = tel
                model_name = m_name
                checks, metrics = handler(spec.model_dump(), telemetry, state)
            passed, score = aggregate_checks(checks, pass_threshold=0.85 if self.mode == "live" else 0.999)
            error_msg = None
        except Exception as exc:
            checks = [bool_check("unhandled_exception", False, f"{type(exc).__name__}: {exc}")]
            metrics = {}
            passed, score = False, 0.0
            error_msg = f"{type(exc).__name__}: {exc}"

        duration_ms = (time.perf_counter() - t0) * 1000
        return (
            CaseReport(
                id=spec.id,
                category=category,  # type: ignore[arg-type]
                title=spec.title,
                scenario=spec.scenario,
                weight=spec.weight,
                passed=passed,
                score=score,
                duration_ms=round(duration_ms, 2),
                llm_calls=telemetry.llm_calls,
                prompt_tokens=telemetry.prompt_tokens,
                completion_tokens=telemetry.completion_tokens,
                total_tokens=telemetry.total_tokens,
                checks=checks,
                metrics=metrics,
                error=error_msg,
            ),
            model_name,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # 维度一：memory_extraction（6 个场景）
    # ══════════════════════════════════════════════════════════════════════════

    def _case_extract_user_preferences(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, wiki):
            session = _seed_session(workspace, data["messages"], session_id="sess-pref")
            node = MemoryExtractionNode(wiki, _fast_extraction_config(), session=session)
            node.collect(session.messages)
            written = node.flush_pending()

            pages = wiki.list_pages()
            combined_text = "\n\n".join(f"{p.name}\n{p.description}\n{p.content}" for p in pages)
            kw_ratio, missing = score_keyword_groups(
                combined_text, data["expected"]["required_keywords_any_group"]
            )
            prov_state = wiki.provenance.read()
            all_source_ids = [sid for p in pages for sid in p.source_ids]

            checks = [
                bool_check("pages_created", written >= 1 and len(pages) >= 1, f"写入页数={written}"),
                ratio_check(
                    "preference_keywords_recall",
                    kw_ratio,
                    threshold=0.75 if self.mode == "live" else 1.0,
                    detail=f"偏好要素命中率={kw_ratio:.2%}" + (f"，缺失={missing}" if missing else ""),
                ),
                *score_provenance_sources(all_source_ids, prov_state, require_user_role=True),
                bool_check(
                    "pending_queue_drained",
                    node.get_pending_count() == 0,
                    f"处理后剩余批次={node.get_pending_count()}",
                ),
                bool_check(
                    "wiki_index_updated",
                    all(p.name in wiki.load_index() for p in pages),
                    "load_index() 已包含新创建的记忆页",
                ),
            ]
            return checks, {"written_pages": written, "keyword_recall": round(kw_ratio, 4)}

    def _case_consolidate_override_config(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, wiki):
            for init_page in data.get("initial_pages", []):
                seed_src = wiki.provenance.capture(
                    [{"uuid": "seed-u0", "role": "user", "content": init_page["content"]}],
                    "sess-seed",
                )
                wiki.save_page(
                    init_page["name"],
                    init_page["content"],
                    description=init_page["description"],
                    tags=init_page["tags"],
                    related=init_page["related"],
                    source_ids=[s["id"] for s in seed_src],
                )
            before_page = wiki.get_page(data["expected"]["target_page"])
            assert before_page is not None

            session = _seed_session(workspace, data["messages"], session_id="sess-override")
            node = MemoryExtractionNode(wiki, _fast_extraction_config(), session=session)
            node.collect(session.messages)
            written = node.flush_pending()

            updated = wiki.get_page(data["expected"]["target_page"])
            content = updated.content if updated else ""
            must_groups = [[kw] for kw in data["expected"]["must_contain"]]
            retain_ratio, missing = score_keyword_groups(content, must_groups)
            stale_leaked = [
                bad for bad in data["expected"]["must_not_contain_active"] if bad in content
            ]

            checks = [
                bool_check(
                    "same_topic_page_updated_in_place",
                    updated is not None and updated.revision > before_page.revision and len(wiki.list_pages()) == 1,
                    f"版本 {before_page.revision} -> {updated.revision if updated else 'None'}，总页数={len(wiki.list_pages())}",
                ),
                ratio_check(
                    "new_and_valid_facts_retained",
                    retain_ratio,
                    threshold=0.75 if self.mode == "live" else 1.0,
                    weight=1.5,
                    detail=f"新事实与保留项覆盖率={retain_ratio:.2%}" + (f"，缺失={missing}" if missing else ""),
                ),
                bool_check(
                    "stale_facts_overridden",
                    len(stale_leaked) == 0,
                    f"过期配置残留项={stale_leaked}",
                ),
            ]
            return checks, {"written_pages": written, "fact_retention": round(retain_ratio, 4)}

    def _case_skip_ephemeral_chat(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, wiki):
            session = _seed_session(workspace, data["messages"], session_id="sess-ephemeral")
            node = MemoryExtractionNode(wiki, _fast_extraction_config(), session=session)
            node.collect(session.messages)
            written = node.flush_pending()
            stats = node.get_stats()

            checks = [
                bool_check(
                    "zero_pages_written",
                    written == 0 and len(wiki.list_pages()) == 0,
                    f"写入页数={written}，当前总页数={len(wiki.list_pages())}",
                ),
                bool_check(
                    "batch_acknowledged_on_skip",
                    node.get_pending_count() == 0 and stats["total_extractions"] == 1,
                    f"pending_count={node.get_pending_count()}, total_extractions={stats['total_extractions']}",
                ),
            ]
            return checks, {"written_pages": written}

    def _case_reject_assistant_speculation(
        self, data: dict, _telemetry: EvalTelemetry, state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, wiki):
            # 第一阶段：端到端提取验证未经用户确认的助手臆测不会入库
            session = _seed_session(workspace, data["messages"], session_id="sess-speculation")
            node = MemoryExtractionNode(wiki, _fast_extraction_config(), session=session)
            node.collect(session.messages)
            node.flush_pending()

            all_content = "\n".join(p.content for p in wiki.list_pages(include_unavailable=True))
            leaked = [kw for kw in data["expected"]["forbidden_keywords"] if kw in all_content]

            # 第二阶段：直接验证 ExtractionRunner 来源守卫拦截仅含 assistant 来源的写入企图
            state["simulate_rogue_assistant_source"] = True
            sources = wiki.provenance.capture(session.messages, session.session_id)
            assistant_only_ids = [s["id"] for s in sources if s["role"] == "assistant"]
            rogue_schema = ExtractionSchema(
                needs_update=True,
                pages=[
                    WikiPageSchema(
                        action="create",
                        name="违规助手推测页",
                        description="仅含助手来源",
                        tags=["测试"],
                        source_ids=assistant_only_ids,
                        content="猜测用户使用 Neovim 与 Dvorak。",
                    )
                ],
            )
            blocked_by_runner = False
            try:
                ExtractionRunner(wiki).process_extraction_result(rogue_schema, sources=sources)
            except RuntimeError:
                blocked_by_runner = True

            checks = [
                bool_check(
                    "no_speculation_persisted",
                    len(leaked) == 0,
                    f"臆测关键词泄露列表={leaked}",
                ),
                bool_check(
                    "runner_rejects_assistant_only_source",
                    blocked_by_runner and wiki.get_page("违规助手推测页") is None,
                    f"ExtractionRunner 拦截仅助手来源={blocked_by_runner}",
                ),
            ]
            return checks, {"blocked_by_runner": int(blocked_by_runner)}

    def _case_filter_tool_and_summary_noise(
        self, data: dict, telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, wiki):
            raw_messages = data["messages"]
            filtered = filter_memory_messages(raw_messages)
            filtered_ids = [m["uuid"] for m in filtered]
            expected_ids = data["expected"]["filtered_message_uuids"]

            session = _seed_session(workspace, raw_messages, session_id="sess-noise")
            node = MemoryExtractionNode(wiki, _fast_extraction_config(), session=session)
            node.collect(session.messages)
            written = node.flush_pending()

            state_raw = wiki.provenance.path.read_text(encoding="utf-8")
            prompt_blob = "\n".join(telemetry.captured_prompts)
            noise_markers = data["expected"]["noise_markers"]
            noise_in_provenance = [m for m in noise_markers if m in state_raw]
            noise_in_prompt = [m for m in noise_markers if m in prompt_blob]

            wiki_blob = "\n".join(p.content for p in wiki.list_pages())
            must_ratio, missing = score_keyword_groups(
                wiki_blob, [[kw] for kw in data["expected"]["must_contain_in_wiki"]]
            )

            checks = [
                bool_check(
                    "filter_memory_messages_exact",
                    filtered_ids == expected_ids,
                    f"过滤后保留消息 ID={filtered_ids}",
                ),
                bool_check(
                    "zero_noise_in_provenance_and_prompt",
                    not noise_in_provenance and not noise_in_prompt,
                    f"来源文件噪声={noise_in_provenance}，提示词噪声={noise_in_prompt}",
                ),
                ratio_check(
                    "valid_deployment_rule_extracted",
                    must_ratio,
                    threshold=0.5 if self.mode == "live" else 1.0,
                    detail=f"有效发版约定提取率={must_ratio:.2%}" + (f"，缺失={missing}" if missing else ""),
                ),
            ]
            return checks, {"written_pages": written, "filtered_count": len(filtered_ids)}

    def _case_explicit_write_and_dedup(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, wiki):
            session = _seed_session(workspace, data["messages"], session_id="sess-explicit")
            # 使用默认 3 轮门槛验证未达自动提取条件时不会自动触发
            strict_cfg = ExtractionConfig(
                minimum_turns=3,
                minimum_message_tokens_to_init=5000,
                idle_seconds=120,
                cooldown_seconds=300,
            )
            node = MemoryExtractionNode(wiki, strict_cfg, session=session)
            node.collect(session.messages)
            auto_ready_before = node.should_extract()
            pending_before = node.get_pending_count()

            agent_mod.register_extractor(node)
            tool_reply = write_wiki_page(data["explicit_content"])
            pending_after = node.get_pending_count()
            pages = wiki.list_pages()
            wiki_blob = "\n".join(p.content for p in pages)

            checks = [
                bool_check(
                    "below_three_turns_not_auto_triggered",
                    pending_before == 1 and not auto_ready_before,
                    f"主动写入前 pending={pending_before}, should_extract={auto_ready_before}",
                ),
                bool_check(
                    "write_wiki_page_immediate_success",
                    "已写入" in tool_reply and len(pages) >= 1 and "npm" in wiki_blob.lower(),
                    f"工具返回: {tool_reply}",
                ),
                bool_check(
                    "acknowledged_sources_dedup_pending",
                    pending_after == data["expected"]["pending_after_explicit"],
                    f"主动写入后待处理批次={pending_after}（避免后台重复提取）",
                ),
            ]
            return checks, {"written_pages": len(node.last_written_pages)}

    # ══════════════════════════════════════════════════════════════════════════
    # 维度二：memory_provenance_and_retrieval（5 个场景）
    # ══════════════════════════════════════════════════════════════════════════

    def _case_search_and_paginated_read(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, _workspace, wiki):
            for p in data["pages"]:
                if "long_prefix_unit" in p:
                    body = p["long_prefix_unit"] * p["long_prefix_repeat"] + p["tail_evidence"]
                else:
                    body = p["content"]
                src = wiki.provenance.capture(
                    [{"uuid": f"u-{p['name']}", "role": "user", "content": body[:300]}],
                    "sess-search",
                )
                wiki.save_page(
                    p["name"],
                    body,
                    description=p["description"],
                    tags=p["tags"],
                    related=p["related"],
                    source_ids=[s["id"] for s in src],
                )

            q = data["queries"]
            kw_hits = json.loads(search_wiki(q["keyword_query"], limit=3))["items"]
            sem_hits = json.loads(search_wiki(q["semantic_query"], limit=3))["items"]

            reset_wiki_reads()
            first_chunk = read_wiki_content(wiki, q["long_page_name"], offset=0, limit=2000)
            dup_chunk = json.loads(read_wiki_page(q["long_page_name"], offset=0, limit=2000))
            next_offset = first_chunk.get("next_offset")
            second_chunk = (
                read_wiki_content(wiki, q["long_page_name"], offset=next_offset, limit=2000)
                if isinstance(next_offset, int)
                else {}
            )

            checks = [
                bool_check(
                    "keyword_search_hit",
                    any(item["name"] == q["expected_hit"] for item in kw_hits),
                    f"关键词检索命中={[i['name'] for i in kw_hits]}",
                ),
                bool_check(
                    "semantic_search_hit",
                    any(item["name"] == q["expected_hit"] for item in sem_hits),
                    f"语义问题检索命中={[i['name'] for i in sem_hits]}",
                ),
                bool_check(
                    "duplicate_range_deduplicated",
                    dup_chunk.get("duplicate") is True,
                    "同版本同 offset/limit 重复读取返回 duplicate=True",
                ),
                bool_check(
                    "paginated_read_reaches_tail_evidence",
                    isinstance(next_offset, int)
                    and next_offset == 6000
                    and q["tail_keyword"] in second_chunk.get("content", ""),
                    f"next_offset={next_offset}，续读命中尾部幂等键={q['tail_keyword'] in second_chunk.get('content', '')}",
                ),
            ]
            return checks, {"first_chunk_chars": len(first_chunk.get("content", ""))}

    def _case_invalidate_hides_page_immediately(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, _workspace, wiki):
            msg = data["message"]
            sources = wiki.provenance.capture([msg], data["session_id"])
            page_cfg = data["page"]
            wiki.save_page(
                page_cfg["name"],
                page_cfg["content"],
                description=page_cfg["description"],
                tags=page_cfg["tags"],
                source_ids=[s["id"] for s in sources],
            )
            assert wiki.retrievable(wiki.get_page(page_cfg["name"]))

            # 用户删除/撤回该条历史消息
            wiki.provenance.invalidate(data["session_id"], {msg["uuid"]})

            raw_page = wiki.get_page(page_cfg["name"])
            visible_pages = wiki.list_pages()
            index_text = wiki.load_index()
            tool_read = json.loads(read_wiki_page(page_cfg["name"]))
            state_text = wiki.provenance.path.read_text(encoding="utf-8")
            detail = WikiService(wiki.memory_dir).get_page(page_cfg["name"])
            secret = data["secret_marker"]

            checks = [
                bool_check(
                    "page_marked_non_retrievable",
                    raw_page is not None and not wiki.retrievable(raw_page) and raw_page.status == "pending",
                    f"页面状态={raw_page.status if raw_page else None}, retrievable=False",
                ),
                bool_check(
                    "excluded_from_list_and_index",
                    visible_pages == [] and secret not in index_text and page_cfg["name"] not in index_text,
                    "list_pages() 与 load_index() 均已隔离失效页面",
                ),
                bool_check(
                    "read_wiki_page_blocks_stale_body",
                    "error" in tool_read and "content" not in tool_read and secret not in json.dumps(tool_read),
                    f"read_wiki_page 返回隔离提示: {tool_read.get('error')}",
                ),
                bool_check(
                    "withdrawn_source_body_purged_on_disk",
                    detail["sources"][0]["state"] == "withdrawn"
                    and "body" not in detail["sources"][0]
                    and secret not in state_text,
                    ".wiki-state.json 中已彻底抹除撤回消息的原文",
                ),
            ]
            return checks, {}

    def _case_manual_edit_protection(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, _workspace, wiki):
            early_sources = wiki.provenance.capture([data["early_message"]], data["session_id"])
            init_cfg = data["initial_page"]
            page = wiki.save_page(
                init_cfg["name"],
                init_cfg["content"],
                description=init_cfg["description"],
                tags=init_cfg["tags"],
                source_ids=[s["id"] for s in early_sources],
            )

            manual_cfg = data["manual_confirmed"]
            confirmed = wiki.provenance.manual_edit(
                page.name,
                page.revision,
                manual_cfg["content"],
                manual_cfg["description"],
                manual_cfg["tags"],
                manual_cfg["related"],
            )

            # 删除早期聊天会话
            wiki.provenance.invalidate(data["session_id"])

            # 模拟滞后的早期自动提取批次试图写回已被删除的旧事实
            stale_proposal = ExtractionSchema(
                needs_update=True,
                pages=[
                    WikiPageSchema(
                        action="append",
                        name=page.name,
                        content=init_cfg["content"],
                        source_ids=[early_sources[0]["id"]],
                    )
                ],
            )
            stale_written = ExtractionRunner(wiki).process_extraction_result(
                stale_proposal, sources=early_sources, batch_id="stale-batch"
            )

            current = wiki.get_page(page.name)
            detail = WikiService(wiki.memory_dir).get_page(page.name)
            removed_marker = data["removed_fact_marker"]

            checks = [
                bool_check(
                    "manual_source_created_with_diff",
                    confirmed.revision > page.revision
                    and detail["sources"][0]["kind"] == "manual"
                    and f"-1. 执行 {removed_marker}" in detail["sources"][0]["diff"],
                    f"修订号 {page.revision} -> {confirmed.revision}，来源类型=manual",
                ),
                bool_check(
                    "remains_retrievable_after_early_chat_deleted",
                    current is not None and wiki.retrievable(current) and page.name in wiki.load_index(),
                    "早期聊天删除后用户手动确认页面仍然正常可检索",
                ),
                bool_check(
                    "stale_batch_cannot_revive_removed_fact",
                    stale_written == 0
                    and current is not None
                    and current.content == manual_cfg["content"]
                    and removed_marker not in current.content,
                    f"滞后旧批次写入数={stale_written}，正文严格保持手动确认版本",
                ),
            ]
            return checks, {"revision": current.revision if current else 0}

    def _case_tombstone_blocks_stale_restore(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, _workspace, wiki):
            sources = wiki.provenance.capture([data["message"]], data["session_id"])
            page_name = data["page_name"]
            old_page = wiki.save_page(
                page_name,
                data["initial_content"],
                description="实验特性记录",
                tags=["实验"],
                source_ids=[s["id"] for s in sources],
            )
            started_before_delete = time.time() - 5.0

            del_reply = delete_wiki_page(page_name)
            deleted_map = wiki.provenance.read()["deleted_pages"]

            # 1) 旧后台自动任务试图写回
            stale_schema = ExtractionSchema(
                needs_update=True,
                pages=[
                    WikiPageSchema(
                        action="create",
                        name=page_name,
                        content=data["stale_content"],
                        source_ids=[sources[0]["id"]],
                    )
                ],
            )
            auto_written = ExtractionRunner(wiki).process_extraction_result(
                stale_schema, sources=sources, batch_id="old-auto-batch"
            )
            # 2) 删除时间之前发起的旧主动请求也不能恢复
            old_explicit_written = ExtractionRunner(wiki).process_extraction_result(
                stale_schema,
                sources=sources,
                explicit=True,
                started_at=started_before_delete,
            )
            still_missing = wiki.get_page(page_name) is None

            # 3) 删除之后发起的新明确请求允许重建且版本号严格递增
            fresh_sources = wiki.provenance.capture(
                [{"uuid": "u-rebuild-new", "role": "user", "content": data["explicit_rebuild_content"]}],
                "sess-new-explicit",
            )
            rebuild_schema = ExtractionSchema(
                needs_update=True,
                pages=[
                    WikiPageSchema(
                        action="create",
                        name=page_name,
                        content=data["explicit_rebuild_content"],
                        source_ids=[fresh_sources[0]["id"]],
                    )
                ],
            )
            rebuilt_written = ExtractionRunner(wiki).process_extraction_result(
                rebuild_schema,
                sources=fresh_sources,
                explicit=True,
                started_at=time.time() + 1.0,
            )
            rebuilt_page = wiki.get_page(page_name)

            checks = [
                bool_check(
                    "delete_tool_sets_tombstone",
                    "已删除" in del_reply and page_name in deleted_map,
                    f"删除工具结果={del_reply}",
                ),
                bool_check(
                    "tombstone_blocks_stale_auto_and_old_explicit",
                    auto_written == 0 and old_explicit_written == 0 and still_missing,
                    f"旧自动写入={auto_written}，旧主动写入={old_explicit_written}",
                ),
                bool_check(
                    "fresh_explicit_rebuild_monotonic_revision",
                    rebuilt_written == 1
                    and rebuilt_page is not None
                    and rebuilt_page.revision > old_page.revision,
                    f"新明确请求重建版本 {old_page.revision} -> {rebuilt_page.revision if rebuilt_page else None}",
                ),
            ]
            return checks, {}

    def _case_wiki_lint_health_check(
        self, data: dict, telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, _workspace, wiki):
            for p in data["linked_pair"]:
                src = wiki.provenance.capture(
                    [{"uuid": f"u-{p['name']}", "role": "user", "content": p["content"]}],
                    "sess-lint",
                )
                wiki.save_page(
                    p["name"],
                    p["content"],
                    description=p["description"],
                    tags=p["tags"],
                    related=p["related"],
                    source_ids=[s["id"] for s in src],
                )

            orphan = data["orphan_page"]
            orphan_src = wiki.provenance.capture(
                [{"uuid": "u-orphan", "role": "user", "content": orphan["content"]}],
                "sess-orphan",
            )
            wiki.save_page(
                orphan["name"],
                orphan["content"],
                description=orphan["description"],
                tags=orphan["tags"],
                related=orphan["related"],
                source_ids=[s["id"] for s in orphan_src],
            )

            inv = data["invalidated_page"]
            inv_src = wiki.provenance.capture(
                [{"uuid": inv["message_id"], "role": "user", "content": inv["content"]}],
                inv["session_id"],
            )
            wiki.save_page(inv["name"], inv["content"], source_ids=[s["id"] for s in inv_src])
            wiki.provenance.invalidate(inv["session_id"], {inv["message_id"]})

            calls_before = telemetry.llm_calls
            report = WikiService(wiki.memory_dir).lint(semantic=False)
            calls_after = telemetry.llm_calls
            issues = report["issues"]

            broken_details = " | ".join(
                i["detail"] for i in issues if i["issue_type"] == "broken_link" and i["severity"] == "error"
            )
            orphan_names = {
                i["page_name"] for i in issues if i["issue_type"] == "orphan" and i["severity"] == "warning"
            }
            source_warn_pages = {
                i["page_name"] for i in issues if i["issue_type"] == "source" and i["severity"] == "warning"
            }

            checks = [
                bool_check(
                    "rule_lint_invokes_zero_llm",
                    report["semantic"] is False and calls_after == calls_before,
                    f"规则检查模型调用增量={calls_after - calls_before}",
                ),
                bool_check(
                    "detects_broken_wiki_links_and_related",
                    all(target in broken_details for target in data["expected_broken_targets"]),
                    f"断链检测详情: {broken_details}",
                ),
                bool_check(
                    "detects_isolated_orphan_page",
                    data["expected_orphan_name"] in orphan_names,
                    f"孤立页面集合={orphan_names}",
                ),
                bool_check(
                    "detects_withdrawn_source_unavailable_page",
                    data["expected_source_warning_page"] in source_warn_pages,
                    f"来源异常页面集合={source_warn_pages}",
                ),
            ]
            return checks, {"issue_count": len(issues)}

    # ══════════════════════════════════════════════════════════════════════════
    # 维度三：context_compression（4 个场景）
    # ══════════════════════════════════════════════════════════════════════════

    def _case_recursive_compress_and_tool_pair(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, _wiki):
            session = _seed_session(workspace, data["messages"], session_id="sess-comp-pair")
            ctx = init_context_manager(session, workspace)

            # 先单独验证边界预算刚好装下 tool 结果却装不下 assistant(tool_calls) 时，_pick_recent 自动移出孤立 tool
            tail_two_tokens = compressor.count_tokens(session.messages[-2:])
            tail_three_tokens = compressor.count_tokens(session.messages[-3:])
            tight_budget = (tail_two_tokens + tail_three_tokens) // 2
            picked_recent, _picked_old = compressor._pick_recent(session.messages, max_tokens=tight_budget)

            with (
                patch.object(ctx_manager_mod, "build_system_prompt", return_value="系统提示"),
                patch.object(ctx_manager_mod, "COMPRESS_THRESHOLD", 40),
                patch.object(compressor, "_MODEL_MAX_TOKENS", data["window_tokens"]),
                patch.object(compressor, "COMPRESS_RECENT_MAX_TOKENS", tight_budget),
                patch.object(compressor, "COMPRESS_SUMMARY_TARGET_TOKENS", 200),
            ):
                ctx.compress_if_needed()

            reloaded = Session(workspace.sessions_dir, session.session_id)
            comp_events = [e for e in reloaded.events if e.get("kind") == "compression"]
            summary_msgs = [
                m for m in reloaded.messages
                if m.get("role") == "assistant" and str(m.get("content", "")).startswith("[SUMMARY]")
            ]

            checks = [
                bool_check(
                    "pick_recent_evicts_orphan_tool_at_boundary",
                    bool(picked_recent) and picked_recent[0].get("role") != "tool",
                    f"紧预算({tight_budget} tok)下保留窗口首条角色={picked_recent[0].get('role') if picked_recent else None}",
                ),
                bool_check(
                    "compression_completed_and_recursive_merged",
                    [e["meta"]["state"] for e in comp_events] == ["started", "completed"]
                    and len(summary_msgs) == 1
                    and reloaded.history_version == 1,
                    f"压缩事件状态={[e['meta']['state'] for e in comp_events]}，摘要消息条数={len(summary_msgs)}",
                ),
                *score_tool_pair_integrity(reloaded.messages),
                bool_check(
                    "tail_user_turn_preserved",
                    reloaded.messages[-1].get("content") == data["expected"]["tail_user_content"],
                    "最新一条用户消息原样保留在队尾",
                ),
            ]
            return checks, {"after_message_count": len(reloaded.messages)}

    def _case_preserve_unextracted_before_compression(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, wiki):
            session = _seed_session(workspace, data["messages"], session_id="sess-comp-preserve")
            node = MemoryExtractionNode(
                wiki,
                _fast_extraction_config(minimum_turns=10, minimum_message_tokens_to_init=99999, idle_seconds=300),
                session=session,
            )
            agent_mod.register_extractor(node)

            with (
                patch.object(compressor, "_MODEL_MAX_TOKENS", 2000),
                patch.object(compressor, "COMPRESS_RECENT_MAX_TOKENS", data["recent_max_tokens"]),
                patch.object(compressor, "COMPRESS_SUMMARY_TARGET_TOKENS", 180),
            ):
                changed = compressor.summarize(session)

            ext_state = node.indexer.get_state()
            pending_uuids = {m["uuid"] for m in ext_state.pending_messages}
            preserved_ids = set(ext_state.preserved_message_ids)
            expected_subset = set(data["expected"]["preserved_uuids_subset"])

            # 此时会话早期原文已被 [SUMMARY] 覆盖，执行 flush_pending 验证从保留队列中零丢失提取出 Wiki 页面
            written = node.flush_pending()
            wiki_blob = "\n".join(p.content for p in wiki.list_pages())
            kw_ratio, missing = score_keyword_groups(
                wiki_blob, [[kw] for kw in data["expected"]["extracted_keywords"]]
            )

            checks = [
                bool_check(
                    "early_messages_replaced_in_session",
                    changed and session.messages[0]["content"].startswith("[SUMMARY]"),
                    "会话早期原文已被压缩摘要替换",
                ),
                bool_check(
                    "raw_messages_preserved_in_extraction_queue",
                    ext_state.force_pending is True
                    and expected_subset.issubset(pending_uuids)
                    and expected_subset.issubset(preserved_ids),
                    f"force_pending={ext_state.force_pending}, 保留原文条数={len(preserved_ids)}",
                ),
                ratio_check(
                    "knowledge_extracted_after_compression",
                    kw_ratio,
                    threshold=0.5 if self.mode == "live" else 1.0,
                    detail=f"压缩后提取出早期迁移规范关键词覆盖率={kw_ratio:.2%}" + (f"，缺失={missing}" if missing else ""),
                ),
            ]
            return checks, {"written_pages": written}

    def _case_summary_fact_retention_score(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, _wiki):
            session = _seed_session(workspace, data["messages"], session_id="sess-comp-retention")
            with (
                patch.object(compressor, "_MODEL_MAX_TOKENS", 4000),
                patch.object(compressor, "COMPRESS_RECENT_MAX_TOKENS", data["recent_max_tokens"]),
                patch.object(compressor, "COMPRESS_SUMMARY_TARGET_TOKENS", 350),
            ):
                changed = compressor.summarize(session)

            summary_msg = session.messages[0]["content"] if session.messages else ""
            exp = data["expected"]
            checks, metrics = score_summary_retention(
                summary_msg,
                sections=exp["sections"],
                fact_groups=exp["fact_groups"],
                min_section_coverage=exp["min_section_coverage"],
                min_fact_retention=exp["min_fact_retention"],
            )
            checks.insert(0, bool_check("summarize_executed", changed, f"summarize 返回={changed}"))
            return checks, metrics

    def _case_concurrent_modification_abort(
        self, data: dict, _telemetry: EvalTelemetry, state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, _wiki):
            session = _seed_session(workspace, data["messages"], session_id="sess-comp-race")
            ctx = init_context_manager(session, workspace)
            injected = {"done": False}

            def inject_concurrent_write(_msgs: list[dict]) -> None:
                if not injected["done"]:
                    injected["done"] = True
                    other_writer = Session(workspace.sessions_dir, session.session_id)
                    other_writer.add(dict(data["concurrent_message"]))

            state["on_before_chat"] = inject_concurrent_write

            aborted_with_conflict = False
            with (
                patch.object(ctx_manager_mod, "build_system_prompt", return_value="系统提示"),
                patch.object(ctx_manager_mod, "COMPRESS_THRESHOLD", 10),
                patch.object(compressor, "_MODEL_MAX_TOKENS", 2000),
                patch.object(compressor, "COMPRESS_RECENT_MAX_TOKENS", data["recent_max_tokens"]),
                patch.object(compressor, "COMPRESS_SUMMARY_TARGET_TOKENS", 150),
            ):
                try:
                    ctx.compress_if_needed()
                except RuntimeError as exc:
                    aborted_with_conflict = "发生变化" in str(exc)

            reloaded = Session(workspace.sessions_dir, session.session_id)
            uuids_on_disk = [m.get("uuid") for m in reloaded.messages]
            expected_uuids = [m["uuid"] for m in data["messages"]] + [data["concurrent_message"]["uuid"]]
            comp_states = [e["meta"]["state"] for e in reloaded.events if e.get("kind") == "compression"]

            checks = [
                bool_check(
                    "detects_concurrent_session_write",
                    aborted_with_conflict,
                    "检测到压缩期间会话并发变更并抛出保护异常",
                ),
                bool_check(
                    "records_failed_compression_event",
                    comp_states == ["started", "failed"],
                    f"压缩过程事件状态={comp_states}",
                ),
                bool_check(
                    "preserves_all_original_and_concurrent_messages",
                    uuids_on_disk == expected_uuids
                    and not any(str(m.get("content", "")).startswith("[SUMMARY]") for m in reloaded.messages),
                    f"磁盘保留消息 UUID={uuids_on_disk}",
                ),
            ]
            return checks, {"message_count": len(reloaded.messages)}

    # ══════════════════════════════════════════════════════════════════════════
    # 维度四：agent_routing_and_tools（5 个场景）
    # ══════════════════════════════════════════════════════════════════════════

    def _case_router_direct_vs_plan_dag(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (tmp_path, workspace, _wiki):
            router = RouterNode()

            # 1) 简单概念问题 -> Direct
            direct_session = _seed_session(
                workspace,
                [{"role": "user", "content": data["direct_prompt"]}],
                session_id="sess-route-direct",
            )
            direct_ctx = init_context_manager(direct_session, workspace)
            direct_res = router.run(data["direct_prompt"])

            # 2) 复杂多阶段任务 -> Plan DAG
            project_dir = tmp_path / "demo_fullstack"
            project_dir.mkdir(parents=True, exist_ok=True)
            plan_prompt = data["plan_prompt"].format(project_path=str(project_dir))
            plan_session = _seed_session(
                workspace,
                [{"role": "user", "content": plan_prompt}],
                session_id="sess-route-plan",
            )
            plan_ctx = init_context_manager(plan_session, workspace)
            plan_res = router.run(plan_prompt)

            steps = plan_res.plan.steps if plan_res.plan else []
            dag_checks, dag_metrics = score_plan_dag(
                steps,
                min_steps=data["expected"]["min_plan_steps"],
                require_parallel=data["expected"]["require_parallel_layer"],
                require_dependency=data["expected"]["require_dependency_edge"],
            )

            checks = [
                bool_check(
                    "simple_question_routes_to_direct",
                    direct_res.route == "direct" and direct_ctx.plan_state is None,
                    f"简单问答路由结果={direct_res.route}",
                ),
                bool_check(
                    "complex_task_routes_to_plan",
                    plan_res.route == "plan" and plan_res.plan is not None and plan_ctx.plan_state is not None,
                    f"复杂任务路由结果={plan_res.route}，目标={plan_res.plan.goal if plan_res.plan else ''}",
                ),
                *dag_checks,
            ]
            return checks, dag_metrics

    def _case_plan_disabled_skips_router(
        self, data: dict, telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (_tmp, workspace, _wiki):
            prompt = data["user_prompt"]
            session = _seed_session(
                workspace,
                [{"role": "user", "content": prompt}],
                session_id="sess-plan-off",
            )
            init_context_manager(session, workspace)
            runner = GraphRunner()
            silent_console = Console(file=StringIO(), highlight=False)

            with patch.object(runner.router, "run", wraps=runner.router.run) as router_spy:
                reply = runner.run(
                    user_input=prompt,
                    session=session,
                    console=silent_console,
                    workspace=workspace,
                    auto_confirm=True,
                    plan_enabled=False,
                )
                runner.react_loop.stop_memory_extractions(workspace)

            hit_any = any(kw.lower() in reply.lower() for kw in data["expected"]["must_contain_any"])
            checks = [
                bool_check(
                    "router_completely_skipped",
                    router_spy.call_count == 0 and telemetry.router_calls == 0,
                    f"RouterNode.run 调用次数={router_spy.call_count}",
                ),
                bool_check(
                    "react_loop_produced_answer",
                    bool(reply.strip()) and hit_any and len(session.messages) >= 2,
                    f"回复长度={len(reply)} 字符，会话消息数={len(session.messages)}",
                ),
            ]
            return checks, {"reply_chars": len(reply)}

    def _case_react_read_patch_verify(
        self, data: dict, _telemetry: EvalTelemetry, state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (tmp_path, workspace, _wiki):
            target_file = tmp_path / data["target_filename"]
            target_file.write_text(data["initial_file_content"], encoding="utf-8")
            state["file_path"] = str(target_file)

            prompt = data["user_prompt_template"].format(file_path=str(target_file))
            session = _seed_session(
                workspace,
                [{"role": "user", "content": prompt}],
                session_id="sess-react-patch",
            )
            init_context_manager(session, workspace)
            runner = GraphRunner()
            reply = runner.run(
                user_input=prompt,
                session=session,
                console=Console(file=StringIO(), highlight=False),
                workspace=workspace,
                auto_confirm=True,
                plan_enabled=False,
            )
            runner.react_loop.stop_memory_extractions(workspace)

            final_disk = target_file.read_text(encoding="utf-8")
            called_tools = [
                tc["function"]["name"]
                for m in session.messages
                if m.get("role") == "assistant" and m.get("tool_calls")
                for tc in m["tool_calls"]
            ]
            exp = data["expected"]

            checks = [
                bool_check(
                    "used_read_file_and_patch_tools",
                    all(req in called_tools for req in exp["required_tools"]),
                    f"实际工具调用序列={called_tools}",
                ),
                bool_check(
                    "file_updated_accurately_on_disk",
                    all(line in final_disk for line in exp["file_must_contain"])
                    and all(old not in final_disk for old in exp["file_must_not_contain"]),
                    "磁盘文件已精确替换目标行且保留其余配置",
                ),
                bool_check(
                    "final_reply_confirms_updated_values",
                    all(tok in reply for tok in exp["reply_must_contain_any"]),
                    f"最终答复={reply[:120]}",
                ),
            ]
            return checks, {"tool_calls_count": len(called_tools)}

    def _case_tool_error_self_healing(
        self, data: dict, _telemetry: EvalTelemetry, state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace() as (tmp_path, workspace, _wiki):
            target_file = tmp_path / data["target_filename"]
            target_file.write_text(data["initial_file_content"], encoding="utf-8")
            state["file_path"] = str(target_file)

            prompt = data["user_prompt_template"].format(file_path=str(target_file))
            initial_messages: list[dict] = [{"role": "user", "content": prompt}]

            # 在 live 模式下，用真实 patch 工具执行一次错误参数调用，把真实错误反馈注入会话检验模型自愈能力
            if self.mode == "live":
                real_err = filesystem.patch(
                    mode="replace",
                    path=str(target_file),
                    old_string=data["wrong_old_string"],
                    new_string=data["target_new_string"],
                )
                initial_messages.extend([
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_live_seed_err",
                                "type": "function",
                                "function": {
                                    "name": "patch",
                                    "arguments": json.dumps({
                                        "mode": "replace",
                                        "path": str(target_file),
                                        "old_string": data["wrong_old_string"],
                                        "new_string": data["target_new_string"],
                                    }),
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_live_seed_err",
                        "content": real_err,
                    },
                ])

            session = _seed_session(workspace, initial_messages, session_id="sess-self-heal")
            init_context_manager(session, workspace)
            runner = GraphRunner()
            reply = runner.run(
                user_input=prompt,
                session=session,
                console=Console(file=StringIO(), highlight=False),
                workspace=workspace,
                auto_confirm=True,
                plan_enabled=False,
            )
            runner.react_loop.stop_memory_extractions(workspace)

            final_disk = target_file.read_text(encoding="utf-8")
            tool_results = [
                str(m.get("content", "")) for m in session.messages if m.get("role") == "tool"
            ]
            saw_error = any("错误：未找到匹配文本" in tr for tr in tool_results)
            exp = data["expected"]

            checks = [
                bool_check(
                    "graceful_tool_error_captured_in_history",
                    saw_error,
                    "工具参数失配返回结构化错误提示而未中断会话",
                ),
                bool_check(
                    "self_healed_and_patched_file",
                    all(s in final_disk for s in exp["file_must_contain"])
                    and all(s not in final_disk for s in exp["file_must_not_contain"]),
                    f"自愈后文件内容={final_disk.strip()!r}",
                ),
                bool_check(
                    "final_reply_reports_port_9090",
                    "9090" in reply,
                    f"最终答复={reply[:120]}",
                ),
            ]
            return checks, {"tool_turns": len(tool_results)}

    def _case_workspace_security_boundary(
        self, data: dict, _telemetry: EvalTelemetry, _state: dict
    ) -> tuple[list[CheckItem], dict]:
        with isolated_eval_workspace(agent_id="sandboxed-eval") as (tmp_path, workspace, _wiki):
            outside_secret = tmp_path / data["outside_filename"]
            outside_secret.write_text(data["outside_secret_content"], encoding="utf-8")
            inside_note = workspace.root / data["inside_filename"]
            inside_note.write_text(data["inside_content"], encoding="utf-8")

            session = _seed_session(
                workspace,
                [{"role": "user", "content": data["user_prompt"]}],
                session_id="sess-sec-boundary",
            )
            init_context_manager(session, workspace)
            runner = GraphRunner()

            with patch("qrclaw.sandbox.is_sandbox_enabled", return_value=True):
                # 同时验证底层防线本身对越界路径的直接拦截能力
                direct_escape_blocked = False
                try:
                    filesystem._resolve_path("../outside_credentials.env")
                except PermissionError:
                    direct_escape_blocked = True

                sensitive_blocked_msg = filesystem.write_file(
                    data["sensitive_target_path"], "forbidden=1"
                )

                reply = runner.run(
                    user_input=data["user_prompt"],
                    session=session,
                    console=Console(file=StringIO(), highlight=False),
                    workspace=workspace,
                    auto_confirm=True,
                    plan_enabled=False,
                )
            runner.react_loop.stop_memory_extractions(workspace)

            secret_token = data["secret_token"]
            full_session_dump = json.dumps(session.messages, ensure_ascii=False)
            forbidden_target = Path(data["sensitive_target_path"])

            checks = [
                bool_check(
                    "sandbox_blocks_parent_traversal_read",
                    direct_escape_blocked,
                    "沙箱路径解析对 ../outside_credentials.env 抛出 PermissionError",
                ),
                bool_check(
                    "blocks_sensitive_etc_write",
                    "拒绝写入敏感系统路径" in sensitive_blocked_msg and not forbidden_target.exists(),
                    f"敏感路径拦截返回: {sensitive_blocked_msg.splitlines()[0]}",
                ),
                bool_check(
                    "zero_secret_leakage_in_session_and_reply",
                    secret_token not in full_session_dump and secret_token not in reply,
                    "外部机密凭证未出现在任何工具结果或助手回复中",
                ),
                bool_check(
                    "agent_completes_safely_within_workspace",
                    bool(reply.strip()) and not reply.startswith("错误：连续"),
                    f"Agent 安全处理越界拒绝并正常答复: {reply[:100]}",
                ),
            ]
            return checks, {}


def render_report(report: EvalReport, console: Console | None = None) -> None:
    """使用 Rich 在终端渲染维度汇总表与每条评测用例的得分详情。"""
    out = console or Console()

    summary_table = Table(
        title=f"QRClaw 记忆与 Agent 效果回归评测汇总（模式: {report.mode} | 模型: {report.model}）",
        show_lines=False,
    )
    summary_table.add_column("评测维度", style="cyan", no_wrap=True)
    summary_table.add_column("中文说明", style="white")
    summary_table.add_column("通过/总数", justify="right")
    summary_table.add_column("加权得分", justify="right", style="bold")
    summary_table.add_column("LLM 调用", justify="right")
    summary_table.add_column("Tokens", justify="right")
    summary_table.add_column("耗时 (ms)", justify="right")

    for cat in report.categories:
        score_style = "green" if cat.weighted_score >= 0.95 else ("yellow" if cat.weighted_score >= 0.8 else "red")
        summary_table.add_row(
            cat.category,
            cat.title,
            f"{cat.passed_cases}/{cat.total_cases}",
            f"[{score_style}]{cat.weighted_score * 100:.1f}%[/{score_style}]",
            str(cat.llm_calls),
            f"{cat.total_tokens:,}",
            f"{cat.duration_ms:.1f}",
        )

    out.print()
    out.print(summary_table)

    case_table = Table(title="评测场景明细", show_lines=False)
    case_table.add_column("状态", justify="center", no_wrap=True)
    case_table.add_column("用例 ID", style="dim", no_wrap=True)
    case_table.add_column("场景描述")
    case_table.add_column("得分", justify="right")
    case_table.add_column("耗时 (ms)", justify="right")

    for case in report.cases:
        badge = "[bold green]PASS[/bold green]" if case.passed else "[bold red]FAIL[/bold red]"
        case_table.add_row(
            badge,
            case.id,
            case.title,
            f"{case.score * 100:.1f}%",
            f"{case.duration_ms:.1f}",
        )
        if not case.passed:
            for chk in case.checks:
                if not chk.passed:
                    case_table.add_row("", f"  ↳ {chk.name}", f"[red]{chk.detail}[/red]", f"{chk.score * 100:.0f}%", "")

    out.print()
    out.print(case_table)

    overall_color = "green" if report.weighted_score >= 0.95 and report.passed_cases == report.total_cases else "yellow"
    out.print(
        Panel(
            f"总通过数: [bold]{report.passed_cases}/{report.total_cases}[/bold] ({report.pass_rate * 100:.1f}%)  |  "
            f"综合加权得分: [bold {overall_color}]{report.weighted_score * 100:.2f}%[/bold {overall_color}]  |  "
            f"总 LLM 调用: {report.total_llm_calls}  |  总 Tokens: {report.total_tokens:,}  |  "
            f"总耗时: {report.duration_ms:.1f} ms",
            title="评测结论",
            border_style=overall_color,
        )
    )


def run_evaluations(
    *,
    mode: EvalMode = "offline",
    categories: set[str] | None = None,
    case_ids: set[str] | None = None,
    output_path: Path | None = None,
    quiet: bool = False,
    console: Console | None = None,
) -> EvalReport:
    """运行评测集并按需渲染终端报告与保存 JSON 结果文件。"""
    runner = EvalRunner(mode=mode)
    report = runner.run(categories=categories, case_ids=case_ids)
    if not quiet:
        render_report(report, console=console)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not quiet:
            (console or Console()).print(f"[dim]已输出 JSON 评测报告至: {output_path}[/dim]")
    return report
