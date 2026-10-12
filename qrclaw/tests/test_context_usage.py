"""上下文用量、压缩进度及历史替换的回归验证。"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qrclaw.memory.context.context_manager import init_context_manager
from qrclaw.memory.context.session import Session
from qrclaw.providers.base import LLMResponse
from qrclaw.server.app import create_app
from qrclaw.workspace import Workspace


def test_context_usage_includes_prompt_and_tools_without_cumulative_usage(temp_dir):
    """上下文用量包含本次提示词和工具定义，累计消耗不影响占用值。"""
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="usage", resume=False)
    session.add({"role": "user", "content": "你好"})
    ctx = init_context_manager(session, workspace)
    tool = {"type": "function", "function": {"name": "read_file", "description": "读取文件",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}
    with patch("qrclaw.memory.context.runtime.manager.build_system_prompt", return_value="使用中文回复。"), \
         patch("qrclaw.memory.context.runtime.manager.get_schemas", return_value=[tool]):
        ctx.build_messages("react")
    before = session.context_tokens
    assert before > session.prompt_tokens
    session.update_tokens(900000, 100000, 1000000)
    restored = Session(workspace.sessions_dir, session.session_id)
    assert restored.context_tokens == before
    with TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        detail = client.get("/v1/sessions/usage").json()
    assert detail["context_tokens"] == before
    assert detail["total_tokens"] == 1000000
    assert 0 < detail["compression_threshold"] < detail["context_limit"]


@pytest.mark.parametrize("failed", [False, True])
def test_compression_events_and_history_survive_reload(temp_dir, failed):
    """压缩过程可续接，成功替换历史，失败保留原始消息。"""
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="compress", resume=False)
    session.add({"role": "user", "content": "旧任务细节。" * 1000})
    session.add({"role": "assistant", "content": "旧任务已完成。" * 1000})
    session.add({"role": "user", "content": "请继续下一步"})
    original = list(session.messages)
    ctx = init_context_manager(session, workspace)
    result = RuntimeError("摘要模型失败") if failed else LLMResponse(content="旧任务已完成，继续下一步。")
    with patch("qrclaw.memory.context.runtime.manager.build_system_prompt", return_value="系统提示"), \
         patch("qrclaw.memory.context.runtime.manager.COMPRESS_THRESHOLD", 100), \
         patch("qrclaw.memory.compression.compressor.COMPRESS_RECENT_MAX_TOKENS", 80), \
         patch("qrclaw.llm_service.get_llm_service") as llm:
        if failed:
            llm.return_value.chat.side_effect = result
            with pytest.raises(RuntimeError, match="摘要模型失败"):
                ctx.compress_if_needed()
        else:
            llm.return_value.chat.return_value = result
            ctx.compress_if_needed()

    saved = Session(workspace.sessions_dir, session.session_id)
    progress = [event for event in saved.events if event["kind"] == "compression"]
    assert [event["meta"]["state"] for event in progress] == ["started", "failed" if failed else "completed"]
    if failed:
        assert saved.messages == original
        assert saved.history_version == 0
    else:
        assert saved.messages[0]["content"].startswith("[SUMMARY]")
        assert saved.messages[-1] == original[-1]
        assert saved.history_version == 1
        assert progress[-1]["meta"]["after_tokens"] == saved.context_tokens
        assert progress[-1]["meta"]["after_tokens"] < progress[-1]["meta"]["before_tokens"]
        assert progress[-1]["meta"]["after_message_count"] == len(saved.messages)
    with TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        stream = client.get("/v1/sessions/compress/events/stream?after=-1").text
    assert '"kind": "compression"' in stream
    assert "旧任务细节" not in stream
    assert "摘要模型失败" not in stream


def test_compression_preserves_events_added_during_summary(temp_dir):
    """摘要生成期间新增的过程记录不能被历史保存覆盖。"""
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="events", resume=False)
    session.add({"role": "user", "content": "旧消息" * 1000})
    ctx = init_context_manager(session, workspace)

    def summary(*_args, **_kwargs):
        """模拟后台子任务在摘要调用期间保存一个状态。"""
        Session(workspace.sessions_dir, session.session_id).add_event({"kind": "status", "title": "步骤完成"})
        return LLMResponse(content="旧消息摘要")

    with patch("qrclaw.memory.context.runtime.manager.COMPRESS_THRESHOLD", 1), \
         patch("qrclaw.memory.compression.compressor.COMPRESS_RECENT_MAX_TOKENS", 1), \
         patch("qrclaw.llm_service.get_llm_service") as llm:
        llm.return_value.chat.side_effect = summary
        ctx.compress_if_needed()
    saved = Session(workspace.sessions_dir, session.session_id)
    assert [event["title"] for event in saved.events] == ["上下文压缩中", "步骤完成", "上下文压缩完成"]
