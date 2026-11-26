"""多维度精细化评分器：覆盖记忆来源校验、事实保留率、工具对完整性与 Plan DAG 拓扑评估。"""
from __future__ import annotations

from qrclaw.graph.executor import get_next_layer
from evals.schemas import CheckItem


def bool_check(name: str, condition: bool, detail: str = "", *, weight: float = 1.0) -> CheckItem:
    """将布尔断言转换为标准评分项。"""
    return CheckItem(
        name=name,
        passed=bool(condition),
        score=1.0 if condition else 0.0,
        weight=weight,
        detail=detail,
    )


def ratio_check(
    name: str,
    ratio: float,
    *,
    threshold: float = 1.0,
    detail: str = "",
    weight: float = 1.0,
) -> CheckItem:
    """将 0~1 连续覆盖率/召回率指标转换为带门禁阈值的评分项。"""
    clamped = max(0.0, min(1.0, float(ratio)))
    return CheckItem(
        name=name,
        passed=clamped + 1e-9 >= threshold,
        score=round(clamped, 4),
        weight=weight,
        detail=detail or f"得分={clamped:.2%} (阈值={threshold:.2%})",
    )


def aggregate_checks(checks: list[CheckItem], *, pass_threshold: float = 0.999) -> tuple[bool, float]:
    """按各检查项权重汇总用例得分与通过状态。"""
    if not checks:
        return False, 0.0
    total_weight = sum(c.weight for c in checks)
    weighted = sum(c.score * c.weight for c in checks) / total_weight if total_weight > 0 else 0.0
    passed = all(c.passed for c in checks) and (weighted + 1e-9 >= pass_threshold)
    return passed, round(weighted, 4)


def score_keyword_groups(text: str, groups: list[list[str]]) -> tuple[float, list[str]]:
    """计算同义词组在目标文本中的召回覆盖率，返回 (覆盖率, 缺失组列表)。"""
    if not groups:
        return 1.0, []
    lowered = text.casefold()
    missing: list[str] = []
    hits = 0
    for group in groups:
        if any(token.casefold() in lowered for token in group):
            hits += 1
        else:
            missing.append("/".join(group))
    return hits / len(groups), missing


def score_provenance_sources(
    source_ids: list[str],
    provenance_state: dict,
    *,
    require_user_role: bool = True,
) -> list[CheckItem]:
    """核验 Wiki 页面绑定的 source_ids 均真实存在、处于 valid 状态且有用户正文支撑。"""
    sources_map: dict = provenance_state.get("sources", {})
    non_empty = bool(source_ids)
    all_valid = non_empty and all(
        sources_map.get(sid, {}).get("state") == "valid" for sid in source_ids
    )
    has_user = non_empty and any(
        sources_map.get(sid, {}).get("role") == "user"
        and sources_map.get(sid, {}).get("state") == "valid"
        for sid in source_ids
    )
    items = [
        bool_check(
            "source_ids_valid",
            all_valid,
            f"绑定来源数={len(source_ids)}，全部有效={all_valid}",
        )
    ]
    if require_user_role:
        items.append(
            bool_check(
                "user_source_supported",
                has_user,
                f"存在真实 user 角色来源支撑={has_user}",
            )
        )
    return items


def score_summary_retention(
    summary_text: str,
    *,
    sections: list[str],
    fact_groups: list[list[str]],
    min_section_coverage: float = 0.75,
    min_fact_retention: float = 0.85,
) -> tuple[list[CheckItem], dict[str, float]]:
    """评估四段式压缩摘要的结构规范度与关键事实保留率。"""
    sec_hits = sum(1 for sec in sections if sec in summary_text)
    sec_ratio = sec_hits / len(sections) if sections else 1.0
    fact_ratio, missing_facts = score_keyword_groups(summary_text, fact_groups)

    checks = [
        bool_check(
            "summary_prefix_marker",
            summary_text.startswith("[SUMMARY]"),
            "摘要消息以 [SUMMARY] 标准前缀开头",
        ),
        ratio_check(
            "four_section_structure",
            sec_ratio,
            threshold=min_section_coverage,
            detail=f"四段式标题命中 {sec_hits}/{len(sections)}",
        ),
        ratio_check(
            "key_fact_retention",
            fact_ratio,
            threshold=min_fact_retention,
            weight=2.0,
            detail=(
                f"关键事实组保留率 {fact_ratio:.2%}"
                + (f"，缺失: {missing_facts}" if missing_facts else "（全部命中）")
            ),
        ),
    ]
    return checks, {
        "section_coverage": round(sec_ratio, 4),
        "fact_retention_rate": round(fact_ratio, 4),
    }


def score_tool_pair_integrity(messages: list[dict]) -> list[CheckItem]:
    """校验压缩保留区的消息序列未拆散 assistant(tool_calls) 与对应的 tool 结果消息。"""
    non_summary = [
        m for m in messages
        if not (m.get("role") == "assistant" and str(m.get("content", "")).startswith("[SUMMARY]"))
    ]
    no_leading_orphan = not (non_summary and non_summary[0].get("role") == "tool")

    active_call_ids: set[str] = set()
    dangling_tools: list[str] = []
    for msg in non_summary:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if isinstance(tc, dict) and tc.get("id"):
                    active_call_ids.add(tc["id"])
        elif role == "tool":
            call_id = msg.get("tool_call_id", "")
            if call_id not in active_call_ids:
                dangling_tools.append(call_id or "<missing>")

    return [
        bool_check(
            "no_leading_orphan_tool",
            no_leading_orphan,
            "保留窗口首条非摘要消息不是孤立的 role=tool",
        ),
        bool_check(
            "all_tool_results_paired",
            len(dangling_tools) == 0,
            f"未配对 tool_call_id 数量={len(dangling_tools)}",
        ),
    ]


def score_plan_dag(
    steps: list,
    *,
    min_steps: int = 3,
    require_parallel: bool = True,
    require_dependency: bool = True,
) -> tuple[list[CheckItem], dict[str, int]]:
    """使用真实的 get_next_layer 拓扑执行器校验 Router 生成的 Plan DAG 无环且分层合理。"""
    step_ids = [str(s.id) for s in steps]
    unique_ids = len(step_ids) == len(set(step_ids))
    known_ids = set(step_ids)
    valid_refs = all(
        str(dep) in known_ids and str(dep) != str(s.id)
        for s in steps
        for dep in (s.depends_on or [])
    )

    remaining = list(steps)
    layers: list[list[str]] = []
    acyclic = True
    while remaining:
        layer = get_next_layer(remaining)
        if not layer:
            acyclic = False
            break
        layers.append([str(s.id) for s in layer])
        done = {s.id for s in layer}
        remaining = [s for s in remaining if s.id not in done]

    has_parallel = any(len(layer) >= 2 for layer in layers)
    has_dependency = any(bool(s.depends_on) for s in steps) and len(layers) >= 2

    checks = [
        bool_check(
            "plan_step_count",
            len(steps) >= min_steps and unique_ids,
            f"步骤数={len(steps)} (要求>={min_steps})，ID 唯一={unique_ids}",
        ),
        bool_check(
            "plan_dag_acyclic_and_valid_refs",
            valid_refs and acyclic,
            f"依赖引用合法={valid_refs}，拓扑无环={acyclic}，共 {len(layers)} 层",
        ),
    ]
    if require_parallel:
        checks.append(
            bool_check(
                "plan_has_parallel_layer",
                has_parallel,
                f"分层拓扑={layers}，存在并行层={has_parallel}",
            )
        )
    if require_dependency:
        checks.append(
            bool_check(
                "plan_has_converging_dependency",
                has_dependency,
                f"存在串行汇聚依赖边={has_dependency}",
            )
        )

    return checks, {"step_count": len(steps), "layer_count": len(layers)}
