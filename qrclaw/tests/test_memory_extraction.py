"""验证真实消息检查点、恢复运行和记忆写入失败的重试范围。"""
from unittest.mock import Mock

import pytest

from qrclaw.graph.nodes.memory_extraction import MemoryExtractionNode
from qrclaw.memory.context.session import Session
from qrclaw.memory.wiki import WikiMemory
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema, WikiPageSchema


def test_extraction_persists_checkpoint_and_resumes(tmp_path):
    """不足旧队列阈值的一次提取也落盘，新运行只读取检查点后的消息。"""
    memory = WikiMemory(tmp_path / "wiki")
    analyzer = Mock()
    analyzer.analyze.return_value = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(
        action="create", name="用户偏好", description="语言", content="用户偏好中文", tags=[], related=[],
    )])
    config = ExtractionConfig(minimum_message_tokens_to_init=1, minimum_tokens_between_update=1)
    session = Session(tmp_path / "sessions", "test")
    session.add({"role": "user", "content": "请记住我偏好中文"})
    session.add({"role": "assistant", "content": "已了解"})
    node = MemoryExtractionNode(memory, config, analyzer)
    assert node.check_and_extract(session)
    assert "偏好中文" in analyzer.analyze.call_args.args[1]
    assert memory.get_page("用户偏好").content == "用户偏好中文"

    resumed = Session(tmp_path / "sessions", "test")
    node = MemoryExtractionNode(memory, config, analyzer)
    assert not node.check_and_extract(resumed)
    resumed.add({"role": "user", "content": "新增信息：项目使用 Python"})
    assert node.check_and_extract(resumed)
    assert "项目使用 Python" in analyzer.analyze.call_args.args[1]
    assert "偏好中文" not in analyzer.analyze.call_args.args[1]


def test_failed_extraction_retries_same_messages(tmp_path):
    """模型或写入失败均不推进检查点，下次运行继续处理原始消息。"""
    memory = WikiMemory(tmp_path / "wiki")
    analyzer = Mock()
    analyzer.analyze.side_effect = RuntimeError("模型暂时不可用")
    config = ExtractionConfig(minimum_message_tokens_to_init=1)
    session = Session(tmp_path / "sessions", "retry")
    session.add({"role": "user", "content": "保留待提取内容"})
    original = session._path.read_bytes()
    node = MemoryExtractionNode(memory, config, analyzer)
    with pytest.raises(RuntimeError, match="分析失败"):
        node.check_and_extract(session)
    assert session._path.read_bytes() == original
    analyzer.analyze.side_effect = None
    analyzer.analyze.return_value = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(
        action="create", name="偏好", description="", content="信息", tags=[], related=[],
    )])
    memory.save_page = Mock(side_effect=OSError("磁盘写入失败"))
    with pytest.raises(OSError):
        node.check_and_extract(session)
    assert session._path.read_bytes() == original
    analyzer.analyze.return_value = ExtractionSchema(needs_update=False, pages=[])
    assert node.check_and_extract(session)
    assert "保留待提取内容" in analyzer.analyze.call_args.args[1]


def test_extraction_waits_for_threshold_and_complete_tools(tmp_path):
    """短对话和未完成工具组不触发提取。"""
    session = Session(tmp_path, "threshold")
    session.add({"role": "user", "content": "你好"})
    analyzer = Mock()
    node = MemoryExtractionNode(WikiMemory(tmp_path / "wiki"), llm_analyzer=analyzer)
    assert not node.check_and_extract(session)
    node.config.minimum_message_tokens_to_init = 1
    session.add({"role": "assistant", "content": "", "tool_calls": [{"id": "t", "type": "function", "function": {"name": "read", "arguments": "{}"}}]})
    assert not node.check_and_extract(session)
    analyzer.analyze.assert_not_called()
