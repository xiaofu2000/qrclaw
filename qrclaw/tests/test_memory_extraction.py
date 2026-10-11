"""Wiki 记忆提取的触发、结构化分析与落盘测试。"""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from qrclaw.graph.nodes.memory_extraction import MemoryExtractionIntegration, MemoryExtractionNode
from qrclaw.graph.nodes.react_loop import ReactLoopNode
from qrclaw.llm_service import LLMService
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.llm import WikiLLMAnalyzer
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema, WikiPageSchema
from qrclaw.memory.wiki.wiki_memory import WikiMemory
from qrclaw.providers.base import LLMResponse
from qrclaw.tools.wiki_tools import write_wiki_page
from qrclaw.workspace import Workspace


def _config(**overrides):
    values = dict(minimum_message_tokens_to_init=10, minimum_tokens_between_update=5)
    values.update(overrides)
    return ExtractionConfig(**values)


def _result(action="create", name="用户偏好", content="喜欢简洁回复"):
    return ExtractionSchema(
        needs_update=True,
        pages=[WikiPageSchema(action=action, name=name, content=content, description="回复风格", tags=["偏好"])],
    )


def test_thresholds_and_optional_tool_gate(temp_dir):
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config())
    assert not node.should_extract([], 9)
    assert node.should_extract([], 10)

    node._tokens_at_last_extraction = 10
    assert not node.should_extract([], 14)
    assert node.should_extract([], 15)

    gated = MemoryExtractionNode(WikiMemory(temp_dir), _config(tool_calls_between_updates=2))
    messages = [{"role": "assistant", "tool_calls": [{"id": "1"}]}]
    assert not gated.should_extract(messages, 10)
    messages[0]["tool_calls"].append({"id": "2"})
    assert gated.should_extract(messages, 10)


def test_queued_messages_use_previous_cutoff(temp_dir):
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config())
    messages = [
        {"role": "user", "content": "第一条偏好", "uuid": "u1"},
        {"role": "assistant", "content": "已记录", "uuid": "a1"},
    ]
    assert node.check_and_extract(messages, 10)
    assert "第一条偏好" in node._pending_extractions[0]["messages_text"]

    messages.append({"role": "user", "content": "第二条偏好", "uuid": "u2"})
    assert node.check_and_extract(messages, 15)
    second = node._pending_extractions[1]["messages_text"]
    assert "第二条偏好" in second
    assert "第一条偏好" not in second


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


def test_existing_page_is_appended_even_when_llm_says_create(temp_dir):
    wiki = WikiMemory(temp_dir)
    wiki.save_page("用户偏好", "原有偏好")
    analyzer = MagicMock()
    analyzer.analyze.return_value = _result(content="新增偏好")
    node = MemoryExtractionNode(wiki, _config(), analyzer)

    assert node.write_now("新增偏好") == 1
    assert "原有偏好" in wiki.get_page("用户偏好").content
    assert "新增偏好" in wiki.get_page("用户偏好").content
    assert node.write_now("新增偏好") == 0
    assert wiki.get_page("用户偏好").content.count("新增偏好") == 1
    analyzer.consolidate.assert_not_called()


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

    with patch("qrclaw.graph.nodes.memory_extraction.threading.Thread") as thread:
        thread.return_value.start.side_effect = lambda: thread.call_args.kwargs["target"]()
        MemoryExtractionIntegration(node).on_react_loop_end(session)

    assert thread.call_args.kwargs["daemon"] is False
    assert node.memory.get_page("用户偏好") is not None


def test_background_worker_drains_queue_without_duplicate_worker(temp_dir):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def analyze(*_):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return _result(name=f"偏好{len(calls)}")

    analyzer = MagicMock()
    analyzer.analyze.side_effect = analyze
    node = MemoryExtractionNode(WikiMemory(temp_dir), _config(max_pending=1), analyzer)
    assert node.check_and_extract([{"role": "user", "content": "偏好一", "uuid": "u1"}], 10)
    assert node.check_and_extract([{"role": "user", "content": "偏好一", "uuid": "u1"}, {"role": "user", "content": "偏好二", "uuid": "u2"}], 15)
    integration = MemoryExtractionIntegration(node)
    integration.on_react_loop_end(SimpleNamespace(messages=[]))
    try:
        assert started.wait(5)
        worker = integration._worker
        integration.on_react_loop_end(SimpleNamespace(messages=[]))
        assert integration._worker is worker
    finally:
        release.set()
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
    first = react._get_memory_integration(Workspace("one", _root=temp_dir / "one"))
    second = react._get_memory_integration(Workspace("two", _root=temp_dir / "two"))

    assert first is not second
    assert second.extractor.memory.memory_dir == temp_dir / "two" / "memory"


def test_default_and_custom_config():
    default = ExtractionConfig()
    assert default.minimum_message_tokens_to_init < 10000
    assert default.minimum_tokens_between_update < 5000
    assert default.tool_calls_between_updates == 0

    custom = _config(tool_calls_between_updates=5)
    assert custom.tool_calls_between_updates == 5
