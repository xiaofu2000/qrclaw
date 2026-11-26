"""记忆整理（做梦）、单页历史版本与整库归档版本的全链路测试。

完整覆盖 docs/memory-dreaming-design.md 第十一节全部 14 个核心测试要点及 Web API。
"""
from __future__ import annotations

import json
import os
import threading
import time
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest

from qrclaw.config import load_settings, settings
from qrclaw.memory.context.session import Session, list_sessions
from qrclaw.memory.wiki import WikiMemory
from qrclaw.memory.wiki.extraction import ExtractionRunner
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.schemas import (
    ConsolidatePageSchema,
    ExtractionSchema,
    WikiPageSchema,
)
from qrclaw.providers.base import LLMResponse, ToolCall
from qrclaw.server.app import create_app
from qrclaw.server.session_service import SessionService
from qrclaw.tools.dream_tools import (
    DreamToolContext,
    dream_tool_context,
    ensure_dream_tools_registered,
    memory_create,
    memory_delete,
    memory_insert,
    memory_rename,
    memory_str_replace,
    memory_view,
)
from qrclaw.tools.registry import AgentType, get_schemas, get_schemas_for_sub_agent
from qrclaw.tools.wiki_tools import read_wiki_content, reset_wiki_reads
from qrclaw.workspace import Workspace


class ScriptedDreamLLM:
    """按预设脚本依次返回模型工具调用或结束回复，用于确定性驱动做梦循环。"""

    def __init__(self, responses: list[LLMResponse | callable]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []
        self.tools_seen: list[list[str]] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **_kwargs) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        self.tools_seen.append([t["function"]["name"] for t in (tools or [])])
        step = self._responses.pop(0) if self._responses else LLMResponse(content="整理完成")
        return step(messages) if callable(step) else step


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(_root=tmp_path)
    reset_wiki_reads()
    return ws


@pytest.fixture
def wiki(workspace, monkeypatch):
    memory = WikiMemory.for_workspace(workspace.memory_dir)
    worker = memory.repair_worker
    monkeypatch.setattr(worker, "resume", lambda: None)
    worker.config = ExtractionConfig(idle_seconds=0, cooldown_seconds=0)
    worker.runner.llm_analyzer = MagicMock()
    worker.runner.llm_analyzer.consolidate.return_value = ConsolidatePageSchema(
        content="修复后正文", tags=[], related=[]
    )
    yield memory
    worker.close()
    memory.dream_worker.close()


# ── 要点 1：工具隔离、路径穿越拦截与单页大小上限 ─────────────────────────────

def test_dream_tools_whitelist_path_security_and_size_limit(tmp_path):
    """记忆工具传入 ../、/、反斜杠、换行、超长名称均被拒绝；副本外文件不受影响；主/子 Agent 不可见。"""
    import qrclaw.tools  # noqa: F401

    ensure_dream_tools_registered()
    main_names = {s["function"]["name"] for s in get_schemas(AgentType.MAIN)}
    sub_names = {s["function"]["name"] for s in get_schemas_for_sub_agent()}
    dream_names = {s["function"]["name"] for s in get_schemas(AgentType.DREAM)}

    expected_dream_tools = {
        "memory_view", "memory_create", "memory_str_replace",
        "memory_insert", "memory_delete", "memory_rename",
        "transcript_list", "transcript_read",
    }
    assert dream_names == expected_dream_tools
    assert not (main_names & expected_dream_tools)
    assert not (sub_names & expected_dream_tools)

    outside_secret = tmp_path / "secret.md"
    outside_secret.write_text("机密内容", encoding="utf-8")
    copy_dir = tmp_path / "dream_copy" / "pages"
    copy_dir.mkdir(parents=True)

    ctx = DreamToolContext(pages_dir=copy_dir, deleted_pages={"已删页面"}, max_page_bytes=1024)
    bad_names = [
        "../secret",
        "../../secret",
        "a/b",
        "a\\b",
        "line\nbreak",
        "line\rbreak",
        "..",
        ".",
        "   ",
        " padded ",
        "超" * 90,
    ]
    with dream_tool_context(ctx):
        for bad in bad_names:
            assert "error" in json.loads(memory_create(bad, "内容"))
            assert "error" in json.loads(memory_view(bad))
            assert "error" in json.loads(memory_delete(bad))
            assert "error" in json.loads(memory_str_replace(bad, "a", "b"))
            assert "error" in json.loads(memory_insert(bad, 0, "x"))
            assert "error" in json.loads(memory_rename(bad, "合法名"))
            assert "error" in json.loads(memory_rename("合法名", bad))

        # 已删除名单中的页面不允许创建或重命名恢复
        assert "禁止恢复" in json.loads(memory_create("已删页面", "试图恢复"))["error"]

        # 单页大小超出上限被拒绝，不产生残缺文件
        assert "超过上限" in json.loads(memory_create("超大页", "字" * 600))["error"]
        assert not (copy_dir / "超大页.md").exists()

        # 合法操作只影响副本目录内部
        assert json.loads(memory_create("QRClaw 规范", "第一行\n第二行", description="规范", tags=["QRClaw"]))["created"] == "QRClaw 规范"
        assert json.loads(memory_str_replace("QRClaw 规范", "第二行", "更新后的第二行"))["updated"] == "QRClaw 规范"
        assert json.loads(memory_insert("QRClaw 规范", 2, "第三行"))["total_lines"] == 3
        viewed = json.loads(memory_view("QRClaw 规范"))
        assert "更新后的第二行" in viewed["content"] and "第三行" in viewed["content"]
        assert json.loads(memory_rename("QRClaw 规范", "QRClaw 编码规范"))["to"] == "QRClaw 编码规范"
        assert json.loads(memory_delete("QRClaw 编码规范"))["deleted"] == "QRClaw 编码规范"

    assert outside_secret.read_text(encoding="utf-8") == "机密内容"


# ── 要点 2 & 4：运行期间原库照常读写不变；做梦对话不进会话列表也不触发提取 ────

def test_dream_runs_isolated_without_polluting_original_wiki_or_sessions(workspace, wiki):
    """做梦运行期间原库可正常读写且不被副本污染；做梦自身对话不进会话列表、不触发提取。"""
    wiki.save_page("旧项目杂记", "旧内容 v1", description="初始描述")
    session = Session(workspace.sessions_dir, session_id="main-1", resume=False)
    session.add({"uuid": "u1", "role": "user", "content": "我习惯使用 4 空格缩进"})
    session.add({"uuid": "a1", "role": "assistant", "content": "已了解你的缩进偏好。"})

    in_dream = threading.Event()
    proceed = threading.Event()

    def step_modify_and_pause(_messages):
        in_dream.set()
        assert proceed.wait(timeout=5)
        return LLMResponse(
            content="正在整理副本",
            tool_calls=[
                ToolCall("tc1", "memory_create", json.dumps({
                    "name": "编码缩进偏好",
                    "content": "Python 与通用代码统一使用 4 空格缩进。",
                    "description": "代码缩进偏好",
                    "tags": ["偏好"],
                }, ensure_ascii=False)),
                ToolCall("tc2", "memory_delete", json.dumps({"name": "旧项目杂记"}, ensure_ascii=False)),
            ],
            finish_reason="tool_calls",
            prompt_tokens=120,
            completion_tokens=40,
            total_tokens=160,
        )

    llm = ScriptedDreamLLM([step_modify_and_pause, LLMResponse(content="整理完毕", prompt_tokens=80, completion_tokens=20, total_tokens=100)])
    started = wiki.dream_worker.start(
        session_ids=["main-1"],
        instructions="重点提炼编码风格偏好",
        sessions_dir=workspace.sessions_dir,
        llm_service=llm,
        background=True,
    )
    assert in_dream.wait(timeout=5)

    # 做梦运行期间，原库可以照常读取与写入新页面，互不阻塞
    assert wiki.get_page("旧项目杂记").content == "旧内容 v1"
    wiki.save_page("运行期新增页", "运行期间写入的原库内容", description="并发页")
    assert wiki.get_page("运行期新增页") is not None

    proceed.set()
    finished = wiki.dream_worker.wait(started["id"], timeout=5)
    assert finished["status"] == "completed"
    assert finished["steps"] == 2
    assert finished["usage"]["total_tokens"] == 260

    # 切换前原库完全不受副本删除/新增影响
    assert wiki.get_page("旧项目杂记") is not None
    assert wiki.get_page("编码缩进偏好") is None

    # 做梦对话不会进入会话列表，也不会产生任何 .extractions 批次
    session_ids = [s["id"] for s in list_sessions(workspace.sessions_dir)]
    assert session_ids == ["main-1"]
    extractions_dir = workspace.memory_dir / ".extractions"
    assert not extractions_dir.exists() or list(extractions_dir.glob("*.json")) == []


# ── 要点 3：压缩过的会话能读到压缩前原文，已撤销来源读不到，排除 sub-* ─────────

def test_dream_transcript_merges_compressed_sources_and_hides_withdrawn_and_subagents(workspace, wiki):
    """压缩改写为 [SUMMARY] 的长会话仍能拼回早期原文，已删改撤销的消息与子会话不可读。"""
    service = SessionService(workspace)
    sess = service.create_session("chat-compressed")
    early_messages = [
        {"uuid": "u_early_keep", "role": "user", "content": "项目部署在杭州机房，端口 8080"},
        {"uuid": "a_early_keep", "role": "assistant", "content": "好的，记录杭州机房 8080。"},
        {"uuid": "u_withdrawn", "role": "user", "content": "临时密码是 secret-999（稍后删除）"},
    ]
    # 模拟压缩前已由 provenance 捕获原文，随后 u_withdrawn 被用户撤销
    wiki.provenance.capture(early_messages, sess.session_id)
    wiki.provenance.invalidate(sess.session_id, {"u_withdrawn"})

    # 模拟会话压缩后，文件里只剩 [SUMMARY] + 近期新对话 + 工具噪音
    sess.messages = [
        {"uuid": "sum1", "role": "assistant", "content": "[SUMMARY] 早期对话摘要，不含细节"},
        {"uuid": "tc_msg", "role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"uuid": "tool_msg", "role": "tool", "content": "内部工具输出"},
        {"uuid": "u_recent", "role": "user", "content": "数据库改用 PostgreSQL 16"},
        {"uuid": "a_recent", "role": "assistant", "content": "明白，已切换为 PostgreSQL 16。"},
    ]
    sess._save()

    # 子 Agent 会话不应被默认选中，显式传入也应拒绝
    sub = Session(workspace.sessions_dir, session_id="sub-worker-1", resume=False)
    sub.add({"uuid": "su1", "role": "user", "content": "子任务执行过程"})

    selected = wiki.dream_worker.select_default_sessions(workspace.sessions_dir)
    assert "chat-compressed" in selected
    assert "sub-worker-1" not in selected
    with pytest.raises(ValueError):
        wiki.dream_worker.start(session_ids=["sub-worker-1"], background=False)

    transcript = wiki.dream_worker.build_session_transcript("chat-compressed", workspace.sessions_dir)
    contents = [m["content"] for m in transcript["messages"]]
    joined = "\n".join(contents)

    assert "项目部署在杭州机房，端口 8080" in joined
    assert "数据库改用 PostgreSQL 16" in joined
    assert "secret-999" not in joined
    assert "[SUMMARY]" not in joined
    assert "内部工具输出" not in joined
    # 确保早期原文排在近期消息之前
    assert contents.index("项目部署在杭州机房，端口 8080") < contents.index("数据库改用 PostgreSQL 16")


# ── 要点 5, 6, 7, 8, 9, 10：切换冲突带上、排除删除页、归档、清基线、并发提取 CAS、无删除标记重建 ──

def test_apply_handles_concurrent_edits_deletes_baselines_cas_and_recreation(workspace, wiki):
    """完整验证切换时的改动带上、删除排除、旧基线清理、并发提取失败重试及移除页重新创建。"""
    # 初始页面：
    # 1. 待合并旧页（做梦后将被删除并合并进新页）
    # 2. 做梦修改页（先建立旧基线，验证切换后旧基线被清掉！）
    # 3. 并发更新页（做梦期间日常提取修改了它，切换时 carry_over=True 带上）
    # 4. 并发删除页（做梦期间用户主动删除了它，切换时从新库排除）
    p_old = wiki.save_page("旧散乱笔记", "散乱的早期记录", description="旧笔记")
    p_dream = wiki.save_page("QRClaw 配置", "旧库遗留的错误配置 old-flag=true", description="配置说明")
    wiki.provenance.baseline(p_dream)
    assert "QRClaw 配置" in wiki.provenance.read()["baselines"]

    src_concurrent = wiki.provenance.capture(
        [{"uuid": "uc1", "role": "user", "content": "并发期间确认 Redis 缓存 TTL 为 600 秒"}],
        "sess-concurrent",
    )
    p_concurrent = wiki.save_page(
        "Redis 缓存", "初始 TTL 60 秒", description="缓存", source_ids=[src_concurrent[0]["id"]]
    )
    wiki.save_page("待删除页", "用户即将删除的内容", description="待删")

    dream_ready = threading.Event()
    allow_finish = threading.Event()

    def run_dream_steps(_messages):
        dream_ready.set()
        assert allow_finish.wait(timeout=5)
        return LLMResponse(
            content="整理页面",
            tool_calls=[
                ToolCall("1", "memory_delete", json.dumps({"name": "旧散乱笔记"}, ensure_ascii=False)),
                ToolCall("2", "memory_str_replace", json.dumps({
                    "name": "QRClaw 配置",
                    "old_str": "旧库遗留的错误配置 old-flag=true",
                    "new_str": "整理后的新配置 new-flag=true",
                    "description": "整理后的配置",
                }, ensure_ascii=False)),
                ToolCall("3", "memory_create", json.dumps({
                    "name": "QRClaw 架构总览",
                    "content": "融合整理后的清晰架构说明。",
                    "description": "架构总览",
                    "tags": ["QRClaw"],
                }, ensure_ascii=False)),
            ],
            finish_reason="tool_calls",
        )

    llm = ScriptedDreamLLM([run_dream_steps, LLMResponse(content="完成")])
    started = wiki.dream_worker.start(session_ids=[], llm_service=llm, background=True)
    assert dream_ready.wait(timeout=5)

    # 在做梦运行期间：
    # (a) 原库更新了「Redis 缓存」并新增了「并发新页」
    wiki.save_page(
        "Redis 缓存",
        "并发更新：Redis TTL 600 秒",
        description="最新缓存配置",
        expected_page=p_concurrent,
        source_ids=[src_concurrent[0]["id"]],
    )
    wiki.save_page("并发新页", "整理期间新写入的页面", description="新页", source_ids=[src_concurrent[0]["id"]])
    # (b) 用户主动删除了「待删除页」
    wiki.delete_page("待删除页")

    allow_finish.set()
    done = wiki.dream_worker.wait(started["id"], timeout=5)
    assert done["status"] == "completed"

    # 要点 5 & 6：检查切换预览
    preview = wiki.dream_worker.apply_preview(started["id"])
    assert preview["can_apply"] is True
    assert [m["name"] for m in preview["concurrent_modified"]] == ["Redis 缓存"]
    assert [a["name"] for a in preview["concurrent_added"]] == ["并发新页"]
    assert preview["excluded_deleted"] == ["待删除页"]
    assert "排除" in preview["warning"]

    # 要点 9：模拟一个正在进行的后台提取，它在切换前读取了「QRClaw 配置」旧快照
    stale_snapshot = wiki.get_page("QRClaw 配置")
    inflight_sources = wiki.provenance.capture(
        [{"uuid": "u_inflight", "role": "user", "content": "补充日志级别为 INFO"}],
        "sess-inflight",
    )
    proposal = ExtractionSchema(
        needs_update=True,
        pages=[WikiPageSchema(
            action="append",
            name="QRClaw 配置",
            content="日志级别 INFO",
            source_ids=[inflight_sources[0]["id"]],
        )],
    )
    extraction_entered = threading.Event()
    switch_completed = threading.Event()
    analyzer = MagicMock()

    def slow_consolidate(**kwargs):
        if not switch_completed.is_set():
            extraction_entered.set()
            assert switch_completed.wait(timeout=5)
            return ConsolidatePageSchema(content=kwargs["existing_content"] + "\n基于旧库的过期合并")
        return ConsolidatePageSchema(content=kwargs["existing_content"] + "\n日志级别 INFO")

    analyzer.consolidate.side_effect = slow_consolidate
    runner = ExtractionRunner(wiki, llm_analyzer=analyzer)

    err_holder: list[Exception] = []

    def run_inflight():
        try:
            runner.process_extraction_result(proposal, sources=inflight_sources, batch_id="b-inflight")
        except Exception as exc:
            err_holder.append(exc)

    t = threading.Thread(target=run_inflight)
    t.start()
    assert extraction_entered.wait(timeout=5)

    # 执行切换（carry_over=True）
    applied = wiki.dream_worker.apply(started["id"], carry_over=True)
    switch_completed.set()
    t.join(timeout=5)

    # 正在进行的提取因为版本号已在切换中递增而 CAS 失败，不会把旧库内容写回新库
    assert len(err_holder) == 1
    assert isinstance(err_holder[0], RuntimeError)

    # 要点 7：验证新库状态、整库归档、索引重建和版本号单调递增
    assert applied["applied"] is True
    store_versions = wiki.store_versions.list_versions()
    assert len(store_versions) == 1 and store_versions[0]["id"] == applied["archived_version"]

    dream_cfg_page = wiki.get_page("QRClaw 配置")
    assert dream_cfg_page.content == "整理后的新配置 new-flag=true"
    assert dream_cfg_page.status == "legacy"
    assert dream_cfg_page.source_ids == []
    assert dream_cfg_page.revision > stale_snapshot.revision
    assert read_wiki_content(wiki, "QRClaw 配置")["source_status"] == "来源未追溯"

    # 带上的并发页面保留了最新正文与来源状态
    carried_redis = wiki.get_page("Redis 缓存")
    assert carried_redis.content == "并发更新：Redis TTL 600 秒"
    assert carried_redis.status == "ready"
    assert carried_redis.source_ids == [src_concurrent[0]["id"]]
    assert wiki.get_page("并发新页") is not None

    # 整理期间用户删除的页面被排除，做梦移除的旧页面也不在新库中
    assert wiki.get_page("待删除页") is None
    assert wiki.get_page("旧散乱笔记") is None
    index_names = {e["name"] for e in wiki.index.all_entries()}
    assert index_names == {"QRClaw 配置", "QRClaw 架构总览", "Redis 缓存", "并发新页"}

    # 要点 9 续：失败的提取批次基于切换后的新库重试成功！
    assert runner.process_extraction_result(proposal, sources=inflight_sources, batch_id="b-inflight") == 1
    updated_cfg = wiki.get_page("QRClaw 配置")
    assert "整理后的新配置 new-flag=true" in updated_cfg.content
    assert "日志级别 INFO" in updated_cfg.content
    assert "old-flag=true" not in updated_cfg.content

    # 要点 8：旧基线在切换时已被清除；刚刚追加的新来源此时触发了基于「做梦新正文」的新基线。
    # 撤销刚刚那条新来源后，repair 重建只包含做梦新正文，绝不会复活切换前的 old-flag=true！
    wiki.provenance.invalidate("sess-inflight")
    repair_llm = wiki.repair_worker.runner.llm_analyzer
    repair_llm.consolidate.return_value = ConsolidatePageSchema(
        content="整理后的新配置 new-flag=true", tags=[], related=[]
    )
    assert wiki.repair_worker.process_due(force=True) == 1
    repair_evidence = repair_llm.consolidate.call_args.kwargs["new_content"]
    assert "整理后的新配置 new-flag=true" in repair_evidence
    assert "old-flag=true" not in repair_evidence

    # 要点 10：被做梦移除的旧页面「旧散乱笔记」没有写删除标记，后续新对话提取可以正常重新创建！
    assert "旧散乱笔记" not in wiki.provenance.read()["deleted_pages"]
    fresh_src = wiki.provenance.capture(
        [{"uuid": "u_recreate", "role": "user", "content": "重新整理一份新的散乱笔记"}],
        "sess-recreate",
    )
    recreate_schema = ExtractionSchema(
        needs_update=True,
        pages=[WikiPageSchema(
            action="create",
            name="旧散乱笔记",
            content="全新创建的笔记内容",
            source_ids=[fresh_src[0]["id"]],
        )],
    )
    assert ExtractionRunner(wiki).process_extraction_result(recreate_schema, sources=fresh_src) == 1
    recreated = wiki.get_page("旧散乱笔记")
    assert recreated is not None and recreated.revision > p_old.revision


# ── 要点 11：整库回退与保留策略 ──────────────────────────────────────────────

def test_store_versions_restore_and_retention_policy(wiki, monkeypatch):
    """整库回退恢复目标版本并将当前库自动归档；保留策略遵守 30 天与至少 5 份。"""
    p1 = wiki.save_page("页面甲", "第一版甲", description="甲")
    v1 = wiki.store_versions.archive_current(reason="初始备份")

    wiki.save_page("页面甲", "第二版甲", description="甲更新")
    wiki.save_page("页面乙", "新增乙", description="乙")

    res = wiki.store_versions.restore(v1["id"])
    assert res["restored_version"] == v1["id"]
    assert wiki.get_page("页面甲").content == "第一版甲"
    assert wiki.get_page("页面甲").revision > p1.revision
    assert wiki.get_page("页面乙") is None

    # 当前库在回退前也被归档了，现在至少有 2 份整库版本
    versions = wiki.store_versions.list_versions()
    assert len(versions) == 2

    # 测试整库清理策略：先放宽上限写入 8 份超过 30 天的旧归档，再按保留最近 5 份清理
    monkeypatch.setattr(settings.memory, "store_versions_keep", 20)
    monkeypatch.setattr(settings.memory, "store_versions_days", 30)
    old_ts = time.time() - 40 * 86400
    for idx in range(6):
        meta = wiki.store_versions.archive_current(reason=f"旧归档-{idx}")
        meta_path = wiki.store_versions._version_dir(meta["id"]) / "meta.json"
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        data["created_at"] = old_ts + idx
        meta_path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(settings.memory, "store_versions_keep", 5)
    removed = wiki.store_versions.cleanup()
    assert removed == 3
    remaining = wiki.store_versions.list_versions()
    assert len(remaining) == 5


# ── 要点 12：取消、步数上限、Token 上限、服务重启恢复与归档丢弃 ─────────────────

def test_dream_cancel_limits_restart_recovery_and_archive(workspace, wiki, monkeypatch):
    """验证取消、步数超限、Token 超限、服务重启中断恢复，均正确记录状态并保留已改副本。"""
    wiki.save_page("原页面", "原始内容")

    # 1. 运行中取消：保留副本已写出的页面
    entered = threading.Event()
    proceed = threading.Event()

    def step_then_wait(_msgs):
        entered.set()
        assert proceed.wait(timeout=5)
        return LLMResponse(
            content="先建一页",
            tool_calls=[ToolCall("c1", "memory_create", json.dumps({"name": "半成品页", "content": "已整理部分"}))],
            finish_reason="tool_calls",
        )

    llm_cancel = ScriptedDreamLLM([step_then_wait, LLMResponse(content="后续步骤")])
    d_cancel = wiki.dream_worker.start(session_ids=[], llm_service=llm_cancel, background=True)
    assert entered.wait(timeout=5)
    wiki.dream_worker.cancel(d_cancel["id"])
    proceed.set()
    res_cancel = wiki.dream_worker.wait(d_cancel["id"], timeout=5)
    assert res_cancel["status"] == "canceled"
    assert wiki.dream_worker.get_dream_page(d_cancel["id"], "半成品页")["content"] == "已整理部分"

    # 2. 超出最大步数上限
    monkeypatch.setattr(settings.memory, "dream_max_steps", 2)
    llm_steps = ScriptedDreamLLM([
        LLMResponse(content="", tool_calls=[ToolCall("s1", "memory_view", "{}")], finish_reason="tool_calls"),
        LLMResponse(content="", tool_calls=[ToolCall("s2", "memory_create", json.dumps({"name": "步数页", "content": "保留"}))], finish_reason="tool_calls"),
        LLMResponse(content="不会到第三步"),
    ])
    res_steps = wiki.dream_worker.start(session_ids=[], llm_service=llm_steps, background=False)
    assert res_steps["status"] == "failed"
    assert "步数" in res_steps["error"]
    assert wiki.dream_worker.get_dream_page(res_steps["id"], "步数页")["content"] == "保留"
    monkeypatch.setattr(settings.memory, "dream_max_steps", 200)

    # 3. 超出单次 Token 上限
    monkeypatch.setattr(settings.memory, "dream_max_tokens", 500)
    llm_tokens = ScriptedDreamLLM([
        LLMResponse(
            content="",
            tool_calls=[ToolCall("t1", "memory_create", json.dumps({"name": "高耗页", "content": "已保存副本"}))],
            finish_reason="tool_calls",
            prompt_tokens=400,
            completion_tokens=200,
            total_tokens=600,
        ),
    ])
    res_tokens = wiki.dream_worker.start(session_ids=[], llm_service=llm_tokens, background=False)
    assert res_tokens["status"] == "failed"
    assert "Token" in res_tokens["error"]
    assert wiki.dream_worker.get_dream_page(res_tokens["id"], "高耗页")["content"] == "已保存副本"
    monkeypatch.setattr(settings.memory, "dream_max_tokens", 2_000_000)

    # 4. 模拟服务崩溃遗留 running 任务 → create_app 启动时自动标记为 failed（服务重启中断）且保留副本
    crashed_id = "drm_20261126_010000_crash1"
    crashed_dir = wiki.dream_worker._dream_dir(crashed_id)
    (crashed_dir / "pages").mkdir(parents=True, exist_ok=True)
    (crashed_dir / "pages" / "崩溃前页.md").write_text("崩溃前保存的正文", encoding="utf-8")
    (crashed_dir / "dream.json").write_text(
        json.dumps({"id": crashed_id, "status": "running", "session_ids": [], "base_revisions": {}, "created_at": time.time()}),
        encoding="utf-8",
    )
    with TestClient(create_app(workspace), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)):
        recovered = wiki.dream_worker.get_dream(crashed_id)
        assert recovered["status"] == "failed"
        assert recovered["error"] == "服务重启中断"
        assert (crashed_dir / "pages" / "崩溃前页.md").exists()

    # 5. 放弃并归档：删除副本目录，默认列表不再显示
    archived = wiki.dream_worker.archive(crashed_id)
    assert archived["archived_at"] is not None
    assert not (crashed_dir / "pages").exists()
    listed_ids = [item["id"] for item in wiki.dream_worker.list_dreams()["items"]]
    assert crashed_id not in listed_ids
    all_ids = [item["id"] for item in wiki.dream_worker.list_dreams(include_archived=True)["items"]]
    assert crashed_id in all_ids


# ── 要点 13 & 14：单页历史版本、撤销消息同步删除历史、单页回滚生成 manual 来源 ────

def test_page_history_purge_on_withdrawal_and_manual_restore(workspace, wiki, monkeypatch):
    """删除聊天消息同步清除关联单页历史版本；单页回滚复用 manual_edit 生成 manual 来源且版本号递增。"""
    s1 = wiki.provenance.capture([{"uuid": "m1", "role": "user", "content": "常用语言是 Python"}], "s-hist")
    s2 = wiki.provenance.capture([{"uuid": "m2", "role": "user", "content": "私有测试密钥 key-123"}], "s-hist")

    # r1: 只引用 s1
    p_r1 = wiki.save_page("开发偏好", "常用语言是 Python", description="v1", source_ids=[s1[0]["id"]])
    assert p_r1.revision == 1
    # r2: 引用 s1 + s2（此时 r1 被自动存入 .history/开发偏好/r1.md）
    p_r2 = wiki.save_page(
        "开发偏好",
        "常用语言是 Python；私有测试密钥 key-123",
        description="v2",
        source_ids=[s1[0]["id"], s2[0]["id"]],
    )
    assert p_r2.revision == 2
    # r3: 再次更新（此时 r2 被自动存入 .history/开发偏好/r2.md）
    p_r3 = wiki.save_page(
        "开发偏好",
        "常用语言是 Python；私有测试密钥 key-123；使用 pytest",
        description="v3",
        source_ids=[s1[0]["id"], s2[0]["id"]],
    )
    assert p_r3.revision == 3
    assert [h["revision"] for h in wiki.history.list_versions("开发偏好")] == [2, 1]

    # 要点 13：用户撤销消息 m2（s2） → 引用 s2 的 r2.md 必须被同步彻底删除，而仅引用 s1 的 r1.md 保留！
    wiki.provenance.invalidate("s-hist", {"m2"})
    versions_after_invalidate = wiki.history.list_versions("开发偏好")
    assert [h["revision"] for h in versions_after_invalidate] == [1]
    history_dir = wiki.history._page_dir("开发偏好")
    for f in history_dir.glob("*.md"):
        assert "key-123" not in f.read_text(encoding="utf-8")

    # 先让 repair 完成，再验证要点 14：单页回滚到 r1
    wiki.repair_worker.runner.llm_analyzer.consolidate.return_value = ConsolidatePageSchema(
        content="常用语言是 Python（修复版）", tags=[], related=[]
    )
    assert wiki.repair_worker.process_due(force=True) == 1
    repaired = wiki.get_page("开发偏好")

    # 错误的 current_revision 会被拒绝
    with pytest.raises(RuntimeError):
        wiki.history.restore("开发偏好", 1, current_revision=repaired.revision - 1)

    restored = wiki.history.restore("开发偏好", 1, current_revision=repaired.revision)
    assert restored.revision > repaired.revision
    assert restored.content == "常用语言是 Python"
    assert restored.status == "ready"
    detail = wiki.provenance.detail(restored)
    assert detail["sources"][0]["kind"] == "manual"

    # 单页历史保留策略：超过 20 个且早于 30 天的旧快照被清理，最近至少保留 20 个
    monkeypatch.setattr(settings.memory, "history_keep", 3)
    monkeypatch.setattr(settings.memory, "history_days", 30)
    old_time = time.time() - 45 * 86400
    for idx in range(5):
        wiki.save_page("清理测试页", f"内容 {idx}", description=f"d{idx}")
    pdir = wiki.history._page_dir("清理测试页")
    for rfile in pdir.glob("r*.md"):
        os.utime(rfile, (old_time, old_time))
    removed = wiki.history.cleanup_page("清理测试页")
    assert removed >= 1
    assert len(wiki.history.list_versions("清理测试页")) == 3


# ── 第七节 & 第八节：全套 HTTP 接口与配置覆盖验证 ─────────────────────────────

def test_dream_and_version_http_endpoints_and_config_overrides(workspace, wiki, monkeypatch):
    """验证 /v1/wiki/dreams、/store-versions、/pages/{name}/history、/status 及配置项映射。"""
    cfg = load_settings(
        {"memory": {"dream_default_sessions": 15, "history_keep": 25}},
        env={"MEMORY_DREAM_MAX_STEPS": "88", "MEMORY_STORE_VERSIONS_KEEP": "8"},
    )
    assert cfg.memory.dream_default_sessions == 15
    assert cfg.memory.dream_max_steps == 88
    assert cfg.memory.store_versions_keep == 8
    assert cfg.memory.history_keep == 25

    wiki.save_page("API测试页", "初始正文 v1", description="初版")
    wiki.save_page("API测试页", "修改后正文 v2", description="二版")

    fake_llm = ScriptedDreamLLM([
        LLMResponse(
            content="查看目录并新增总结页",
            tool_calls=[
                ToolCall("v1", "memory_view", "{}"),
                ToolCall("c1", "memory_create", json.dumps({
                    "name": "API整理总结",
                    "content": "通过 HTTP 接口完成整理的总结内容。",
                    "description": "整理总结",
                    "tags": ["API"],
                }, ensure_ascii=False)),
            ],
            finish_reason="tool_calls",
            prompt_tokens=50,
            completion_tokens=30,
            total_tokens=80,
        ),
        LLMResponse(content="全部整理完毕", prompt_tokens=30, completion_tokens=10, total_tokens=40),
    ])
    monkeypatch.setattr("qrclaw.llm_service.get_llm_service", lambda: fake_llm)

    with TestClient(create_app(workspace), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        # 1. 单页历史查询与回滚接口
        hist = client.get("/v1/wiki/pages/API测试页/history").json()
        assert hist["total"] == 1 and hist["items"][0]["revision"] == 1
        snap = client.get("/v1/wiki/pages/API测试页/history/1").json()
        assert snap["content"] == "初始正文 v1"
        assert client.post("/v1/wiki/pages/API测试页/restore", json={"revision": 1, "current_revision": 999}).status_code == 409
        restored = client.post("/v1/wiki/pages/API测试页/restore", json={"revision": 1, "current_revision": 2})
        assert restored.status_code == 200
        assert restored.json()["content"] == "初始正文 v1"
        assert restored.json()["revision"] == 3

        # 2. 发起做梦、查询详情、浏览新库、读取 transcript、切换预览与正式切换
        created_resp = client.post("/v1/wiki/dreams", json={"instructions": "提炼 API 总结"})
        assert created_resp.status_code == 200
        dream_id = created_resp.json()["id"]
        wiki.dream_worker.wait(dream_id, timeout=5)

        dreams_list = client.get("/v1/wiki/dreams").json()
        assert dreams_list["total"] >= 1 and dreams_list["items"][0]["id"] == dream_id

        dream_detail = client.get(f"/v1/wiki/dreams/{dream_id}").json()
        assert dream_detail["status"] == "completed"
        assert any(a["name"] == "API整理总结" for a in dream_detail["diff"]["added"])

        dpages = client.get(f"/v1/wiki/dreams/{dream_id}/pages").json()
        assert any(p["name"] == "API整理总结" for p in dpages["items"])
        dpage_detail = client.get(f"/v1/wiki/dreams/{dream_id}/pages/API整理总结").json()
        assert "HTTP 接口" in dpage_detail["content"]

        trans = client.get(f"/v1/wiki/dreams/{dream_id}/transcript").json()
        assert trans["total"] == 2
        assert trans["items"][0]["tool_calls"][0]["name"] == "memory_view"

        preview = client.get(f"/v1/wiki/dreams/{dream_id}/apply-preview").json()
        assert preview["can_apply"] is True

        apply_res = client.post(f"/v1/wiki/dreams/{dream_id}/apply", json={"carry_over": True})
        assert apply_res.status_code == 200
        archived_ver = apply_res.json()["archived_version"]
        assert client.get("/v1/wiki/pages/API整理总结").status_code == 200

        # 3. 整库版本查询与回退接口
        sv_list = client.get("/v1/wiki/store-versions").json()
        assert any(v["id"] == archived_ver for v in sv_list["items"])
        sv_restore = client.post(f"/v1/wiki/store-versions/{archived_ver}/restore")
        assert sv_restore.status_code == 200
        assert client.get("/v1/wiki/pages/API整理总结").status_code == 404
        assert client.get("/v1/wiki/pages/API测试页").json()["content"] == "初始正文 v1"

        # 4. 运行中的做梦计入 /v1/wiki/status 的 pending
        running_id = "drm_20261126_020000_run001"
        rdir = wiki.dream_worker._dream_dir(running_id)
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "dream.json").write_text(
            json.dumps({"id": running_id, "status": "running", "session_ids": [], "base_revisions": {}, "created_at": time.time()}),
            encoding="utf-8",
        )
        st = client.get("/v1/wiki/status").json()
        assert st["active_dreams"] >= 1 and st["pending"] >= 1
        assert client.post(f"/v1/wiki/dreams/{running_id}/cancel").status_code == 200
