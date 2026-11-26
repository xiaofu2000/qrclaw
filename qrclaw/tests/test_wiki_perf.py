"""记忆与检索层 3 个性能炸弹修复验证测试。

覆盖：
1. list_pages() 不随页面数量 N 重复解析 .wiki-state.json，load_index() 轻量内存缓存命中与失效；
2. WikiPage.applied_batches 限制保留最近 200 条；
3. provenance.compact() 清理未引用的过期/已删会话来源与孤立撤销记录，同时完整保留有效被引用来源；
4. search_wiki 零 LLM 请求完成中英混合 BM25 检索与最佳片段定位，select_relevant_pages 超 30 页先本地粗筛；
5. MCP search_tools 支持中文及中英混合描述工具检索（最多返回 5 条，保持相同 schema）。
"""
from __future__ import annotations

from contextlib import AsyncExitStack
import json
import time
from unittest.mock import AsyncMock

from mcp.types import ListToolsResult, Tool
import pytest

from qrclaw.mcp_integration.client import MCPManager, _MCPConnection
from qrclaw.memory.wiki import WikiMemory
from qrclaw.tools.wiki_tools import reset_wiki_reads, search_wiki


# ── 性能炸弹 1 & 2：.wiki-state.json 内存缓存、单次读取与索引缓存 ────────────

def test_list_pages_reads_state_once_and_caches_parsed_snapshot(tmp_path, monkeypatch):
    """30 个页面的 list_pages() 单次最多解析 1 次 .wiki-state.json，未变化时重复调用 0 次 json.loads。"""
    wiki = WikiMemory(tmp_path / "memory")
    sources = wiki.provenance.capture(
        [{"uuid": f"u{i}", "role": "user", "content": f"记忆事实 {i}"} for i in range(30)],
        "sess-perf",
    )
    for idx in range(30):
        wiki.save_page(
            f"性能测试页_{idx:02d}",
            f"这是第 {idx} 个记忆页面的正文内容。",
            description=f"摘要 {idx}",
            source_ids=[sources[idx]["id"]],
        )

    real_loads = json.loads
    state_parse_count = 0
    state_text_sample = wiki.provenance.path.read_text(encoding="utf-8")[:30]

    def counting_loads(s, *args, **kwargs):
        nonlocal state_parse_count
        if isinstance(s, str) and s.startswith(state_text_sample[:10]) and '"deleted_pages"' in s:
            state_parse_count += 1
        return real_loads(s, *args, **kwargs)

    # 先强制清空内存签名缓存，测试冷读时的解析次数
    wiki.provenance._cache_sig = None
    wiki.provenance._cache_state = None
    monkeypatch.setattr("qrclaw.memory.wiki.provenance.json.loads", counting_loads)

    pages = wiki.list_pages()
    assert len(pages) == 30
    assert state_parse_count == 1, f"冷读 30 个页面只应解析 1 次 .wiki-state.json，实际: {state_parse_count}"

    # 状态文件未变化时，再次调用 list_pages() 与 retrievable() 全部命中内存缓存（0 次解析）
    state_parse_count = 0
    assert len(wiki.list_pages()) == 30
    assert all(wiki.retrievable(p) for p in pages[:5])
    assert state_parse_count == 0

    # read() 返回安全深拷贝：调用方在内存中改动未写回时，绝不污染只读缓存
    mutated = wiki.provenance.read()
    mutated["deleted_pages"]["性能测试页_00"] = "2026-11-26T00:00:00"
    assert "性能测试页_00" not in wiki.provenance.read_readonly()["deleted_pages"]

    # load_index() 缓存验证：重复构建系统提示词时不重复扫描读取每个页面文件
    wiki.invalidate_cache()
    first_index = wiki.load_index()
    assert "性能测试页_00：摘要 0" in first_index and "性能测试页_29：摘要 29" in first_index

    get_page_calls = 0
    orig_get_page = wiki.get_page

    def counting_get_page(name):
        nonlocal get_page_calls
        get_page_calls += 1
        return orig_get_page(name)

    monkeypatch.setattr(wiki, "get_page", counting_get_page)
    for _ in range(10):
        assert wiki.load_index() == first_index
    assert get_page_calls == 0, "load_index 命中轻量缓存时不应重读页面 Markdown"

    # 修改或新增页面后缓存立即失效，下一轮读取最新索引
    monkeypatch.setattr(wiki, "get_page", orig_get_page)
    wiki.save_page("新增实时页", "新内容", description="实时可见")
    updated_index = wiki.load_index()
    assert "新增实时页：实时可见" in updated_index


def test_applied_batches_capped_and_provenance_compact_preserves_referenced_sources(tmp_path):
    """applied_batches 限制保留最近 200 条；compact() 清理过期未引用来源且绝不误删被引用来源。"""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "active-session.json").write_text('{"messages": []}', encoding="utf-8")

    wiki = WikiMemory(tmp_path / "memory")
    for idx in range(245):
        wiki.save_page("批次回执页", f"内容 {idx}", batch_id=f"batch-{idx:03d}")
    page = wiki.get_page("批次回执页")
    assert len(page.applied_batches) == 200
    assert page.applied_batches[0] == "batch-045"
    assert page.applied_batches[-1] == "batch-244"

    # 准备 4 类来源：
    # 1) 被现有页面引用的旧来源（即使超过 60 天且所属会话文件已删除，也必须 100% 保留！）
    ref_src = wiki.provenance.capture([{"uuid": "u_ref", "role": "user", "content": "长期有效的核心架构决策"}], "deleted-ref-session")
    wiki.save_page("核心决策", "长期有效的核心架构决策", source_ids=[ref_src[0]["id"]])

    # 2) 未被任何页面引用、且所属会话文件已从 sessions_dir 删除的来源 → 应被清理
    unreferenced_deleted_sess = wiki.provenance.capture(
        [{"uuid": "u_skip_del", "role": "user", "content": "闲聊跳过内容（会话已删）"}],
        "deleted-skip-session",
    )

    # 3) 未被页面引用、会话仍存在但超过 30 天的旧跳过来源 → 应被清理
    unreferenced_old = wiki.provenance.capture(
        [{"uuid": "u_skip_old", "role": "user", "content": "两个月前的闲聊"}],
        "active-session",
    )

    # 4) 未被页面引用、属于最近活跃会话的有效来源（供未来做梦拼完整记录） → 应保留
    unreferenced_recent = wiki.provenance.capture(
        [{"uuid": "u_skip_recent", "role": "user", "content": "今天刚聊的未提取上下文"}],
        "active-session",
    )

    # 将 1) 和 3) 的时间改到 60 天前，并添加大量孤立 withdrawn 记录
    old_ts = time.time() - 60 * 86400
    with wiki._lock:
        state = wiki.provenance.read()
        state["sources"][ref_src[0]["id"]]["captured_at"] = old_ts
        state["sources"][unreferenced_old[0]["id"]]["captured_at"] = old_ts
        for i in range(15):
            wid = f"orphan_withdrawn_{i}"
            state["sources"][wid] = {
                "id": wid,
                "session_id": "active-session",
                "message_id": f"m_{i}",
                "role": "user",
                "kind": "chat",
                "state": "withdrawn",
                "captured_at": old_ts if i < 5 else time.time(),
                "order": 1000 + i,
            }
        wiki.provenance.write(state)

    removed = wiki.provenance.compact(sessions_dir=sessions_dir, max_withdrawn=5, keep_days=30, keep_sessions=50)
    assert removed >= 12

    after_sources = wiki.provenance.read()["sources"]
    # 被页面引用的来源完整保留原文且页面可正常检索
    assert ref_src[0]["id"] in after_sources
    assert after_sources[ref_src[0]["id"]]["body"] == "长期有效的核心架构决策"
    assert wiki.retrievable(wiki.get_page("核心决策"))

    # 最近活跃会话的未引用有效来源保留供做梦使用
    assert unreferenced_recent[0]["id"] in after_sources

    # 会话已删或超期的未引用来源已被清除
    assert unreferenced_deleted_sess[0]["id"] not in after_sources
    assert unreferenced_old[0]["id"] not in after_sources

    # 孤立 withdrawn 记录严格控制在 max_withdrawn 以内
    remaining_withdrawn = [s for s in after_sources.values() if s.get("state") == "withdrawn"]
    assert len(remaining_withdrawn) == 5


# ── 性能炸弹 3：零 LLM 的中英混合 BM25 搜索 + Plan 粗筛 + 中文 MCP 工具检索 ──

def test_search_wiki_uses_local_hybrid_bm25_with_zero_llm_calls(tmp_path, monkeypatch):
    """search_wiki 不发起任何模型请求，支持中英混合长查询、字段权重排序与最佳片段定位；Plan 超 30 页先本地粗筛。"""
    reset_wiki_reads()
    wiki = WikiMemory(tmp_path / "memory")
    wiki.save_page(
        "前端配色方案",
        "界面采用深浅双主题配色，与压缩无关。",
        description="UI 主题变量",
        tags=["前端", "样式"],
    )
    wiki.save_page(
        "上下文管理",
        "前导说明。\n" * 40 + "QRClaw 在上下文超过 90% 窗口时自动触发摘要压缩，近期原文预算默认保留 12%。\n" + "后续补充。\n" * 30,
        description="长对话的自动压缩与近期原文预算机制",
        tags=["QRClaw", "上下文"],
    )
    wiki.save_page(
        "QRClaw 预算日志",
        "记录一般开销。",
        description="普通记录",
        tags=["日志"],
    )

    monkeypatch.setattr("qrclaw.tools.wiki_tools._get_wiki_memory", lambda: wiki)

    def forbid_llm(*_args, **_kwargs):
        raise AssertionError("search_wiki 不应调用 LLM 选页模型")

    monkeypatch.setattr("qrclaw.memory.wiki.selection.wiki_selector.chat", forbid_llm)

    res = json.loads(search_wiki("QRClaw 压缩 预算", limit=3))
    items = res["items"]
    assert len(items) >= 1
    assert items[0]["name"] == "上下文管理"
    assert "压缩" in items[0]["snippet"] or "预算" in items[0]["snippet"]
    assert items[0]["offset"] > 0

    # 字段权重：精确名称命中优先于仅正文提及
    wiki.save_page("PostgreSQL 连接池", "连接数上限 20", description="数据库参数")
    wiki.save_page("杂项运维记录", "昨天排查了 PostgreSQL 连接池 的超时现象。" * 3, description="日常流水")
    ranked = json.loads(search_wiki("PostgreSQL 连接池", limit=2))["items"]
    assert ranked[0]["name"] == "PostgreSQL 连接池"

    # Plan 模式 select_relevant_pages：当目录超过 30 页时，先通过本地混合 BM25 粗筛 Top 30 再交给模型
    for i in range(40):
        wiki.save_page(f"无关噪音页_{i:02d}", f"完全不相关的占位文本 {i}", description="噪音")

    seen_prompts: list[str] = []

    def inspect_selector_prompt(messages):
        prompt = messages[0]["content"]
        seen_prompts.append(prompt)
        return '{"selected":[{"name":"上下文管理","reason":"命中压缩与预算","relevance":0.95}]}'

    monkeypatch.setattr("qrclaw.memory.wiki.selection.wiki_selector.chat", inspect_selector_prompt)
    selected = wiki.select_relevant_pages("QRClaw 怎么控制压缩和原文预算？", [], top_k=2)
    assert [p.name for p in selected] == ["上下文管理"]
    assert len(seen_prompts) == 1
    # 45 个总页面粗筛后只注入 30 个候选页面条目，且目标页稳居其中
    all_page_names = {p.name for p in wiki.list_pages()}
    candidate_lines = [
        line for line in seen_prompts[0].splitlines()
        if line.startswith("- ") and line[2:].split("：", 1)[0] in all_page_names
    ]
    assert len(all_page_names) == 45
    assert len(candidate_lines) == 30
    assert any("上下文管理" in line for line in candidate_lines)


@pytest.mark.asyncio
async def test_mcp_search_tools_supports_chinese_and_hybrid_queries(monkeypatch):
    """MCP search_tools 支持中文及中英混合查询，准确召回中文描述工具并保持不超过 5 条与一致的 schema。"""
    manager = MCPManager()
    session = AsyncMock()
    tools = [
        *(
            Tool(name=f"filler_{i}", description="普通无关占位接口", inputSchema={"type": "object"})
            for i in range(25)
        ),
        Tool(
            name="check_stock",
            description="实时核对仓库商品库存余量与批次状态",
            inputSchema={
                "type": "object",
                "properties": {"sku": {"type": "string"}},
                "required": ["sku"],
            },
        ),
        Tool(
            name="sync_calendar",
            description="双向同步 Google Calendar 日历会议日程",
            inputSchema={
                "type": "object",
                "properties": {"day": {"type": "string"}},
                "required": ["day"],
            },
        ),
    ]
    session.list_tools.return_value = ListToolsResult(tools=tools)
    monkeypatch.setattr(
        manager,
        "_connect_stdio",
        AsyncMock(return_value=_MCPConnection(
            label="erp",
            exit_stack=AsyncExitStack(),
            session=session,
            instructions="企业仓储库存与日程协同服务",
        )),
    )

    await manager.connect_all([{"label": "erp"}])
    try:
        stock_hits = json.loads(await manager.call_tool("search_tools", {"query": "核对商品库存余量"}))
        assert 1 <= len(stock_hits) <= 5
        assert stock_hits[0]["name"] == "erp__check_stock"
        assert stock_hits[0]["inputSchema"]["required"] == ["sku"]

        cal_hits = json.loads(await manager.call_tool("search_tools", {"query": "同步 Calendar 日历日程"}))
        assert 1 <= len(cal_hits) <= 5
        assert cal_hits[0]["name"] == "erp__sync_calendar"
        assert cal_hits[0]["inputSchema"]["required"] == ["day"]

        empty_hits = json.loads(await manager.call_tool("search_tools", {"query": "预订机票航班"}))
        assert empty_hits == []
    finally:
        await manager.disconnect_all()
