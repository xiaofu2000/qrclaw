"""项目清理涉及的记忆、缓存和日志回归检查，全程不访问外部服务。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from qrclaw.graph.nodes.wiki_query import WikiQueryNode
from qrclaw.logger.logger import QRClawLogger
from qrclaw.memory.compression import compressor
from qrclaw.memory.context import context_manager
from qrclaw.memory.wiki.selection import WikiPageSelector
from qrclaw.workspace import Workspace


def test_wiki_query_uses_current_workspace(tmp_path, monkeypatch):
    """默认查询应使用线程上下文中的工作区。"""
    workspace = Workspace(_root=tmp_path)
    monkeypatch.setattr(
        context_manager._thread_local,
        "ctx",
        SimpleNamespace(workspace=workspace),
        raising=False,
    )
    assert WikiQueryNode().wiki.memory_dir == workspace.memory_dir


@pytest.mark.parametrize(
    "response",
    [
        "[]",
        '{"selected": [null]}',
        '{"selected": [{"name": "页面", "relevance": 2}]}',
        "invalid",
    ],
)
def test_selector_rejects_invalid_response(response):
    """模型返回错误结构时，应返回空选择结果，不能让解析异常逃逸。"""
    assert WikiPageSelector(Mock())._parse_response(response).selected == []


def test_selector_accepts_fenced_json():
    """保留 Markdown JSON 响应的解析能力。"""
    result = WikiPageSelector(Mock())._parse_response(
        '```json\n{"selected": [{"name": "页面", "relevance": 0.8}]}\n```'
    )
    assert result.selected[0].name == "页面"


def test_plan_changes_invalidate_prompt(tmp_path, monkeypatch):
    """清空 Wiki 上下文、结束或替换计划都必须清除旧提示。"""
    monkeypatch.setattr(
        context_manager, "build_system_prompt", lambda **kwargs: kwargs["wiki_context"]
    )
    context = context_manager.ContextManager(Mock(), Workspace(_root=tmp_path))
    context.set_plan("目标", [])
    for action in (
        lambda: context.set_wiki_context(""),
        context.clear_plan,
        lambda: context.set_plan("新目标", []),
    ):
        context.set_plan("目标", [])
        context.set_wiki_context("旧 Wiki 内容")
        assert context._get_system_prompt() == "旧 Wiki 内容"
        action()
        assert context._get_system_prompt() == ""


def test_compression_keeps_messages_not_in_summary(monkeypatch, tmp_path):
    """压缩不能丢掉尚未摘要的近期消息。"""
    from qrclaw.memory.context.session import Session
    session = Session(tmp_path, resume=False)
    session.add({"role": "assistant", "content": "需要摘要的旧消息"})
    session.add({"role": "user", "content": "尚未摘要的重要消息"})
    recent, old = session.messages[1:], session.messages[:1]
    monkeypatch.setattr(compressor, "_pick_recent", lambda _: (recent, old))
    monkeypatch.setattr("qrclaw.llm_service.LLMService.chat", lambda *a, **k: SimpleNamespace(content="旧消息摘要"))
    compressor.summarize(session)
    assert session.messages[1:] == recent
    assert Session(tmp_path, session_id=session.session_id).messages == session.messages


def test_empty_summary_keeps_original_messages(monkeypatch, tmp_path):
    """空摘要必须保留原始消息和磁盘内容。"""
    from qrclaw.memory.context.session import Session
    session = Session(tmp_path, resume=False)
    session.add({"role": "assistant", "content": "重要历史"})
    messages = session.messages
    original = session._path.read_bytes()
    monkeypatch.setattr(compressor, "_pick_recent", lambda _: ([], messages))
    monkeypatch.setattr("qrclaw.llm_service.LLMService.chat", lambda *a, **k: SimpleNamespace(content=""))
    with pytest.raises(ValueError, match="空摘要"):
        compressor.summarize(session)
    assert session.messages is messages
    assert session._path.read_bytes() == original


def test_same_session_can_change_log_directory(tmp_path):
    """相同会话 ID 切换工作区时，日志必须写入新目录。"""
    manager = QRClawLogger()
    try:
        for directory in (tmp_path / "first", tmp_path / "second"):
            manager.setup(session_id="same", log_dir=directory, log_to_console=False)
        manager.get_logger().info("只写入新目录")
        assert "只写入新目录" not in (tmp_path / "first/qrclaw-same.log").read_text()
        assert "只写入新目录" in (tmp_path / "second/qrclaw-same.log").read_text()
    finally:
        for handler in manager.get_logger().handlers[:]:
            handler.close()
            manager.get_logger().removeHandler(handler)


def test_database_connection_closes_after_transaction(tmp_path):
    """数据库事务完成或失败后都释放连接。"""
    import sqlite3
    from qrclaw.execution.store import RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    with store._connect() as connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with pytest.raises(ValueError):
        with store._connect() as failed_connection:
            raise ValueError("模拟事务失败")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        failed_connection.execute("SELECT 1")


@pytest.mark.parametrize(
    "name", ["", "../outside", "/tmp/outside", "a/b", "a\\b", "a\nb"]
)
def test_wiki_rejects_paths_outside_pages(tmp_path, name):
    """所有页面读写入口都拒绝无效名称和目录穿越。"""
    from qrclaw.memory.wiki import WikiMemory

    wiki = WikiMemory(tmp_path)
    for operation in (
        wiki.get_page,
        wiki.delete_page,
        wiki.page_exists,
        lambda value: wiki.save_page(value, "内容"),
    ):
        with pytest.raises(ValueError):
            operation(name)


def test_wiki_rejects_symlink_escape(tmp_path):
    """页面符号链接不能读取、覆写或删除知识库之外的文件。"""
    from qrclaw.memory.wiki import WikiMemory

    wiki = WikiMemory(tmp_path / "memory")
    outside = tmp_path / "outside.md"
    outside.write_text("外部文件")
    (wiki.pages_dir / "link.md").symlink_to(outside)
    for operation in (
        wiki.get_page,
        wiki.delete_page,
        lambda name: wiki.save_page(name, "修改"),
    ):
        with pytest.raises(ValueError):
            operation("link")
    assert outside.read_text() == "外部文件"


def test_completed_message_replaces_partial_stream():
    """完成事件的完整正文应修正尚未完整显示的流式文本。"""
    from qrclaw.execution.models import EventEnvelope
    from qrclaw.execution.projector import RunProjector, create_snapshot

    projector = RunProjector(create_snapshot("run_test", "conv_test", "测试"))
    for seq, event_type, data in [
        (1, "assistant.delta", {"message_id": "msg", "delta": "部分"}),
        (2, "assistant.completed", {"message_id": "msg", "content": "完整回复"}),
    ]:
        projector.apply(
            EventEnvelope(
                event_id=f"event_{seq}",
                timestamp="2026-09-15T00:00:00Z",
                seq=seq,
                run_id="run_test",
                conversation_id="conv_test",
                type=event_type,
                data=data,
            )
        )
    assert projector.snapshot.messages[0].content == "完整回复"
    assert projector.snapshot.messages[0].completed
