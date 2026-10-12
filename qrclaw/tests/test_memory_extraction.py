"""Wiki 记忆提取的触发、结构化分析与落盘测试。"""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from qrclaw.graph.nodes.memory_extraction import MemoryExtractionIntegration, MemoryExtractionNode
from qrclaw.graph.nodes.react_loop import ReactLoopNode
from qrclaw.llm_service import LLMService
from qrclaw.memory.context.session import Session
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.schemas import ConsolidatePageSchema
from qrclaw.memory.wiki.extraction.llm import WikiLLMAnalyzer
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema, WikiPageSchema
from qrclaw.memory.wiki.wiki_memory import WikiMemory
from qrclaw.providers.base import LLMResponse
from qrclaw.tools.wiki_tools import write_wiki_page
from qrclaw.workspace import Workspace


def _config(**overrides):
    values = dict(minimum_message_tokens_to_init=1, minimum_tokens_between_update=1,
                  idle_seconds=0, cooldown_seconds=0)
    values.update(overrides)
    return ExtractionConfig(**values)


def _result(action="create", name="用户偏好", content="喜欢简洁回复"):
    return ExtractionSchema(
        needs_update=True,
        pages=[WikiPageSchema(action=action, name=name, content=content, description="回复风格", tags=["偏好"])],
    )


def test_threshold_counts_only_unprocessed_body(temp_dir):
    """触发阈值采用实际待提取正文，忽略外部传入的历史 token 总数。"""
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config(minimum_message_tokens_to_init=10))
    messages = [{"role": "user", "content": "字" * 27, "uuid": "u1"}]
    assert not node.check_and_extract(messages, 100000)
    messages[0]["content"] += "字" * 3
    assert node.check_and_extract(messages, 0)


def test_pending_messages_are_merged_without_duplicates(temp_dir):
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config())
    messages = [
        {"role": "user", "content": "第一条偏好", "uuid": "u1"},
        {"role": "assistant", "content": "已记录", "uuid": "a1"},
    ]
    assert node.check_and_extract(messages, 10)
    assert node.get_pending_count() == 1

    messages.append({"role": "user", "content": "第二条偏好", "uuid": "u2"})
    assert node.check_and_extract(messages, 15)
    node.check_and_extract(messages, 100)
    pending = node.indexer.get_state().pending_messages
    assert [m["uuid"] for m in pending] == ["u1", "a1", "u2"]
    assert node.get_pending_count() == 1


def test_flush_persists_page_and_index(temp_dir):
    analyzer = MagicMock()
    analyzer.analyze.return_value = _result()
    wiki = WikiMemory(temp_dir)
    node = MemoryExtractionNode(wiki, _config(), analyzer)
    messages = [{"role": "user", "content": "请记住我喜欢简洁回复", "uuid": "u1"}]

    assert node.check_and_extract(messages, 10)
    assert node.flush_pending() == 1
    assert node.get_pending_count() == 0
    assert "请记住" in analyzer.analyze.call_args.args[1]

    reopened = WikiMemory(temp_dir)
    assert reopened.get_page("用户偏好").content == "喜欢简洁回复"
    assert reopened.index.all_entries()[0]["name"] == "用户偏好"
    assert reopened.search_pages("简洁")[0].name == "用户偏好"
    analyzer.consolidate.assert_not_called()


def test_concurrent_wiki_appends_keep_every_update(temp_dir):
    WikiMemory(temp_dir).save_page("偏好", "起始")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: WikiMemory(temp_dir).append_page("偏好", f"更新{i}"), range(16)))

    wiki = WikiMemory(temp_dir)
    content = wiki.get_page("偏好").content
    assert set(content.split()) == {"起始", *(f"更新{i}" for i in range(16))}
    assert [entry["name"] for entry in wiki.index.all_entries()] == ["偏好"]


def test_corrupt_wiki_index_does_not_overwrite_page(temp_dir):
    wiki = WikiMemory(temp_dir)
    wiki.save_page("偏好", "原有内容")
    (temp_dir / "index.json").write_text("{invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="索引损坏"):
        wiki.save_page("偏好", "新内容")
    with pytest.raises(ValueError, match="索引损坏"):
        wiki.delete_page("偏好")

    assert wiki.get_page("偏好").content == "原有内容"
    assert (temp_dir / "index.json").read_text(encoding="utf-8") == "{invalid"
    wiki.rebuild_index()
    assert wiki.index.all_entries()[0]["name"] == "偏好"


@pytest.mark.parametrize("queued", [False, True])
def test_existing_page_is_merged_even_when_llm_says_create(temp_dir, queued):
    """自动和主动写入都读取旧正文，替换过期事实并保留有效信息。"""
    wiki = WikiMemory(temp_dir)
    original = wiki.save_page("用户偏好", "旧记录“当前端口为9000”已过期；当前端口为8765。\n用户喜欢中文回复。",
                              description="用户配置", tags=["配置"], related=["项目架构"])
    analyzer = MagicMock()
    extraction = _result(name="用户 偏好", content="当前端口为9000")
    extraction.pages[0].related = ["运行环境", "项目架构"]
    analyzer.analyze.return_value = extraction
    analyzer.consolidate.return_value = ConsolidatePageSchema(
        content="当前端口为9000。\n用户喜欢中文回复。", description="用户当前配置", tags=["配置"])
    node = MemoryExtractionNode(wiki, _config(), analyzer)

    if queued:
        assert node.check_and_extract([{"role": "user", "content": "端口改回9000", "uuid": "u1"}], 10)
        assert node.flush_pending() == 1
        assert node.get_pending_count() == 0
    else:
        assert node.write_now("端口改回9000") == 1
    analyzer.consolidate.assert_called_once_with(
        existing_content=original.content, new_content=extraction.pages[0].content, page_name="用户偏好")
    reopened = WikiMemory(temp_dir)
    saved = reopened.get_page("用户偏好")
    assert saved.content == analyzer.consolidate.return_value.content
    assert "8765" not in saved.content
    assert saved.created_at == original.created_at
    assert saved.tags == ["配置", "偏好"]
    assert saved.related == ["项目架构", "运行环境"]
    assert reopened.index.all_entries()[0]["description"] == "用户当前配置"
    assert "用户当前配置" in reopened.load_index()
    assert node.write_now("端口改回9000") == 0


@pytest.mark.parametrize("empty", [False, True])
def test_failed_merge_preserves_page_and_queued_update(temp_dir, empty):
    """合并异常或空正文不能破坏旧页，后台任务保留并可再次处理。"""
    wiki = WikiMemory(temp_dir)
    wiki.save_page("用户偏好", "旧偏好")
    original_files = {name: (temp_dir / name).read_bytes()
                      for name in ["pages/用户偏好.md", "index.json", "index.md", "log.md"]}
    analyzer = MagicMock()
    analyzer.analyze.return_value = _result(content="新偏好")
    if empty:
        analyzer.consolidate.return_value = ConsolidatePageSchema(content=" \n ")
    else:
        analyzer.consolidate.side_effect = RuntimeError("合并模型暂不可用")
    node = MemoryExtractionNode(wiki, _config(), analyzer)
    assert node.check_and_extract([{"role": "user", "content": "更新偏好", "uuid": "u1"}], 10)

    assert node.flush_pending() == 0
    assert node.get_pending_count() == 1
    assert all((temp_dir / name).read_bytes() == value for name, value in original_files.items())
    with patch("qrclaw.agent.get_extractor", return_value=node):
        assert "错误" in write_wiki_page("更新偏好")
    assert wiki.get_page("用户偏好").content == "旧偏好"
    analyzer.consolidate.side_effect = None
    analyzer.consolidate.return_value = ConsolidatePageSchema(content="新偏好")
    assert node.flush_pending() == 1
    assert node.get_pending_count() == 0
    assert wiki.get_page("用户偏好").content == "新偏好"


@pytest.mark.parametrize("change", ["content", "metadata", "delete"])
def test_merge_does_not_overwrite_concurrent_page_changes(temp_dir, change):
    """生成合并结果期间的正文、元数据更新或删除都不能被旧快照覆盖。"""
    wiki = WikiMemory(temp_dir)
    wiki.save_page("用户偏好", "原有偏好", tags=["偏好"])
    analyzer = MagicMock()
    analyzer.analyze.return_value = _result(content="新增偏好")

    def merge(**_kwargs):
        """模拟另一个写入者在模型生成期间修改持久化页面。"""
        other = WikiMemory(temp_dir)
        if change == "delete":
            other.delete_page("用户偏好")
        else:
            other.save_page("用户偏好", "并发更新" if change == "content" else "原有偏好",
                            tags=["新标签"])
        return ConsolidatePageSchema(content="基于旧快照的合并结果")

    analyzer.consolidate.side_effect = merge
    node = MemoryExtractionNode(wiki, _config(), analyzer)
    with pytest.raises(RuntimeError, match="Wiki 页面写入失败"):
        node.write_now("新增偏好")
    saved = wiki.get_page("用户偏好")
    if change == "delete":
        assert saved is None
        assert not wiki.index.all_entries()
    else:
        assert saved.content == ("并发更新" if change == "content" else "原有偏好")
        assert saved.tags == ["新标签"]


def test_merge_reads_legacy_page_without_timestamps(temp_dir):
    """不带 frontmatter 的旧页面也能安全更新。"""
    wiki = WikiMemory(temp_dir)
    (wiki.pages_dir / "用户偏好.md").write_text("原有偏好", encoding="utf-8")
    analyzer = MagicMock()
    analyzer.analyze.return_value = _result(content="新偏好")
    analyzer.consolidate.return_value = ConsolidatePageSchema(content="新偏好")
    node = MemoryExtractionNode(wiki, _config(), analyzer)
    assert node.write_now("新偏好") == 1
    assert wiki.get_page("用户偏好").content == "新偏好"


def test_unreadable_old_page_is_not_replaced(temp_dir):
    """旧页面存在但无法解析时，不按新页面覆盖它。"""
    wiki = WikiMemory(temp_dir)
    path = wiki.pages_dir / "用户偏好.md"
    path.write_text("", encoding="utf-8")
    analyzer = MagicMock()
    analyzer.analyze.return_value = _result()
    node = MemoryExtractionNode(wiki, _config(), analyzer)
    with pytest.raises(RuntimeError, match="Wiki 页面写入失败"):
        node.write_now("新偏好")
    assert path.read_text(encoding="utf-8") == ""


def test_failed_analysis_stays_queued_for_retry(temp_dir):
    analyzer = MagicMock()
    analyzer.analyze.side_effect = RuntimeError("temporary failure")
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config(), analyzer)
    assert node.check_and_extract([{"role": "user", "content": "偏好", "uuid": "u1"}], 10)

    assert node.flush_pending() == 0
    assert node.get_pending_count() == 1

    analyzer.analyze.side_effect = None
    analyzer.analyze.return_value = _result()
    assert node.flush_pending() == 1
    assert node.get_pending_count() == 0


def test_manual_write_reports_real_result(temp_dir):
    analyzer = MagicMock()
    analyzer.analyze.return_value = _result()
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config(), analyzer)

    with patch("qrclaw.agent.get_extractor", return_value=node):
        message = write_wiki_page("用户喜欢简洁回复")

    assert "已写入 1 个 Wiki 页面" in message
    assert node.memory.get_page("用户偏好") is not None


def test_integration_flushes_first_task(temp_dir):
    analyzer = MagicMock()
    analyzer.analyze.return_value = _result()
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config(), analyzer)
    session = SimpleNamespace(messages=[{"role": "assistant", "content": "请记住用户偏好" * 10, "uuid": "a1"}])

    integration = MemoryExtractionIntegration(node)
    with patch("qrclaw.graph.nodes.memory_extraction.Timer"):
        integration.on_react_loop_end(session)
        with patch("qrclaw.graph.nodes.memory_extraction.Thread") as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs["target"]()
            integration._start_worker()
            assert thread.call_args.kwargs["daemon"] is False
        integration.close()
    assert node.memory.get_page("用户偏好") is not None


def test_background_worker_drains_queue_without_duplicate_worker(temp_dir):
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    calls = []

    def analyze(*_):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return _result(name=f"偏好{len(calls)}")

    analyzer = MagicMock()
    analyzer.analyze.side_effect = analyze
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config(), analyzer)
    messages = [{"role": "user", "content": "偏好一", "uuid": "u1"},
                {"role": "user", "content": "偏好二", "uuid": "u2"}]
    integration = MemoryExtractionIntegration(node)
    original_flush = node.flush_pending

    def flush(**kwargs):
        """第二批实际落盘后通知测试，避免只验证模型调用开始。"""
        written = original_flush(**kwargs)
        if node.indexer.get_state().total_extractions == 2:
            completed.set()
        return written

    node.flush_pending = flush
    integration.on_react_loop_end(SimpleNamespace(messages=messages))
    try:
        assert started.wait(5)
        worker = integration._worker
        messages.append({"role": "user", "content": "偏好三", "uuid": "u3"})
        integration.on_react_loop_end(SimpleNamespace(messages=messages))
        assert integration._worker is worker
        release.set()
        assert completed.wait(5)
    finally:
        release.set()
        integration.close()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert calls == [1, 2]
    assert node.get_pending_count() == 0
    assert len(node.memory.list_pages()) == 2


def test_structured_llm_output_is_parsed_without_network():
    class FakeProvider:
        _model = "fake"

        def chat(self, messages, **kwargs):
            return LLMResponse(content='```json\n{"needs_update": true, "pages": [{"action": "create", "name": "用户偏好", "content": "喜欢简洁回复"}]}\n```')

    analyzer = WikiLLMAnalyzer(LLMService(FakeProvider()), _config())
    result = analyzer.analyze("（暂无页面）", "[user] 记住偏好")

    assert result.needs_update
    assert result.pages[0].name == "用户偏好"


def test_structured_merge_receives_both_old_and_new_content(temp_dir):
    """真实分析器和结构化解析把旧正文、新事实交给合并调用，无需联网。"""
    wiki = WikiMemory(temp_dir)
    original = "端口为8765。用户喜欢中文回复。"
    wiki.save_page("项目配置", original)
    prompts = []

    class FakeProvider:
        """返回确定的模型结果，用于验证两次调用的数据传递。"""
        _model = "fake"

        def chat(self, messages, **_kwargs):
            """先定位页面，再模拟生成更新后的完整正文。"""
            prompts.append("\n".join(message["content"] for message in messages))
            if len(prompts) == 1:
                return LLMResponse(content='{"needs_update": true, "pages": [{"action": "append", "name": "项目配置", "content": "端口改为9000"}]}')
            return LLMResponse(content='{"content": "端口为9000。用户喜欢中文回复。", "description": "项目当前配置", "tags": ["配置"]}')

    analyzer = WikiLLMAnalyzer(LLMService(FakeProvider()), _config())
    node = MemoryExtractionNode(wiki, _config(), analyzer)
    assert node.write_now("请记住端口改为9000") == 1
    assert len(prompts) == 2
    assert original in prompts[1]
    assert "端口改为9000" in prompts[1]
    assert wiki.get_page("项目配置").content == "端口为9000。用户喜欢中文回复。"


def test_invalid_page_name_cannot_escape_wiki_directory(temp_dir):
    wiki = WikiMemory(temp_dir)
    with pytest.raises(ValueError, match="无效的 Wiki 页面名称"):
        wiki.save_page("../outside", "不应写出目录")
    assert not (temp_dir / "outside.md").exists()
    assert wiki.get_page("../outside") is None
    assert not wiki.page_exists("../outside")

    analyzer = MagicMock()
    analyzer.analyze.return_value = _result(name="../outside")
    node = MemoryExtractionNode(wiki, _config(), analyzer)
    with pytest.raises(RuntimeError, match="Wiki 页面写入失败"):
        node.write_now("不应写出目录")


def test_partial_write_reports_failed_page(temp_dir):
    analyzer = MagicMock()
    analyzer.analyze.return_value = ExtractionSchema(
        needs_update=True,
        pages=[
            WikiPageSchema(action="create", name="用户偏好", content="简洁回复"),
            WikiPageSchema(action="create", name="../outside", content="不应写出目录"),
        ],
    )
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config(), analyzer)
    with pytest.raises(RuntimeError, match="已写入 1 个"):
        node.write_now("记录偏好")
    assert node.memory.get_page("用户偏好") is not None
    assert not (temp_dir / "outside.md").exists()


def test_semantic_recall_when_keyword_search_misses(temp_dir, monkeypatch):
    wiki = WikiMemory(temp_dir)
    wiki.save_page("回复风格", "尽量简洁")
    monkeypatch.setattr(
        "qrclaw.memory.wiki.selection.WikiPageSelector.select",
        lambda self, query, messages: SimpleNamespace(selected=[SimpleNamespace(name="回复风格")]),
    )

    pages = wiki.select_relevant_pages("怎样回答用户", [], top_k=1)
    assert [page.name for page in pages] == ["回复风格"]


def test_memory_integration_tracks_workspace(temp_dir):
    react = ReactLoopNode()
    first_workspace = Workspace("one", _root=temp_dir / "one")
    second_workspace = Workspace("two", _root=temp_dir / "two")
    first = react._get_memory_integration(first_workspace, Session(first_workspace.sessions_dir, resume=False))
    second = react._get_memory_integration(second_workspace, Session(second_workspace.sessions_dir, resume=False))

    assert first is not second
    assert second.extractor.memory.memory_dir == temp_dir / "two" / "memory"


def test_memory_integration_is_separate_per_session(temp_dir):
    react = ReactLoopNode()
    workspace = Workspace(_root=temp_dir)
    first_session = Session(workspace.sessions_dir, resume=False)
    second_session = Session(workspace.sessions_dir, resume=False)

    first = react._get_memory_integration(workspace, first_session)
    second = react._get_memory_integration(workspace, second_session)

    assert first is not second
    assert react._get_memory_integration(workspace, first_session) is first


def test_default_and_custom_config():
    default = ExtractionConfig()
    assert default.minimum_message_tokens_to_init == 5000
    assert default.minimum_tokens_between_update == 5000
    assert default.minimum_turns == 3
    assert default.idle_seconds == 120
    assert default.cooldown_seconds == 300
    assert default.tool_calls_between_updates == 0

    custom = _config(tool_calls_between_updates=5)
    assert custom.tool_calls_between_updates == 5
