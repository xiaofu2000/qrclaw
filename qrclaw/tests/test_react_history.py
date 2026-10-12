"""工具调用必须能在下一轮会话中回放。"""

from io import StringIO
from unittest.mock import patch

from rich.console import Console

from qrclaw.graph.nodes.react_loop import ReactLoopNode
from qrclaw.memory.context.context_manager import init_context_manager
from qrclaw.memory.context.session import Session
from qrclaw.providers.base import LLMResponse, ToolCall
from qrclaw.workspace import Workspace


def test_tool_exchange_is_saved_in_session(temp_dir):
    workspace = Workspace("worker", _root=temp_dir)
    session = Session(workspace.sessions_dir, resume=False)
    session.add({"role": "user", "content": "运行工具"})
    init_context_manager(session, workspace, is_sub_agent=True)
    responses = iter([
        LLMResponse(content="", tool_calls=[ToolCall("call-1", "read_file", '{}')], finish_reason="tool_calls"),
        LLMResponse(content="完成"),
    ])

    class FakeLLM:
        """按测试顺序返回工具调用与最终答复。"""

        def chat(self, *_args, **_kwargs):
            """返回下一轮预设响应。"""
            return next(responses)

    with patch("qrclaw.graph.nodes.react_loop.get_llm_service", return_value=FakeLLM()), \
         patch("qrclaw.graph.nodes.react_loop.ToolRunner.run", return_value="文件内容"):
        result = ReactLoopNode().run(session, Console(file=StringIO()), workspace, True, is_sub_agent=True)

    restored = Session(workspace.sessions_dir, session.session_id)
    assert result == "完成"
    assert [message["role"] for message in restored.messages] == ["user", "assistant", "tool", "assistant"]
    assert restored.messages[1]["tool_calls"][0]["id"] == "call-1"
    assert restored.messages[2]["tool_call_id"] == "call-1"
    assert [event["title"] for event in restored.events if event["kind"] == "tool_status"] == ["工具开始", "工具完成"]
    assert sum(event["kind"] == "message_saved" for event in restored.events) == 3


def test_stream_keeps_intermediate_text_and_reports_tool_status(temp_dir):
    """带工具调用的中间文字可见，过程事件只包含工具名称和状态。"""
    workspace = Workspace("worker", _root=temp_dir)
    session = Session(workspace.sessions_dir, resume=False)
    session.add({"role": "user", "content": "运行工具"})
    init_context_manager(session, workspace, is_sub_agent=True)
    responses = iter([
        LLMResponse(content="先读取", tool_calls=[ToolCall("call-1", "read_file", '{"secret":"不要直播"}')], finish_reason="tool_calls"),
        LLMResponse(content="完成"),
    ])

    class FakeLLM:
        def chat(self, *_args, on_delta=None, **_kwargs):
            response = next(responses)
            if on_delta:
                on_delta(response.content)
            return response

    deltas = []
    with patch("qrclaw.graph.nodes.react_loop.get_llm_service", return_value=FakeLLM()), \
         patch("qrclaw.graph.nodes.react_loop.ToolRunner.run", return_value="文件内容"), \
         patch.object(ReactLoopNode, "_get_memory_integration", return_value=None):
        result = ReactLoopNode().run(session, Console(file=StringIO()), workspace, True,
                                     is_sub_agent=False, on_delta=deltas.append)

    restored = Session(workspace.sessions_dir, session.session_id)
    assert result == "完成"
    assert deltas == ["先读取", "完成"]
    assert [(event["title"], event["content"]) for event in restored.events if event["kind"] == "tool_status"] == [
        ("工具开始", "read_file"), ("工具完成", "read_file")]
    assert "不要直播" not in str(restored.events)


def test_main_agent_records_tool_failure_without_streaming(temp_dir):
    """非流式主 Agent 也记录失败状态，过程事件不包含工具参数。"""
    workspace = Workspace("worker", _root=temp_dir)
    session = Session(workspace.sessions_dir, resume=False)
    session.add({"role": "user", "content": "运行工具"})
    init_context_manager(session, workspace, is_sub_agent=True)
    responses = iter([
        LLMResponse(content="", tool_calls=[ToolCall("call-1", "read_file", '{"secret":"不要直播"}')], finish_reason="tool_calls"),
        LLMResponse(content="完成"),
    ])

    class FakeLLM:
        """按测试顺序返回工具调用与最终答复。"""

        def chat(self, *_args, **_kwargs):
            """返回下一轮预设响应。"""
            return next(responses)

    with patch("qrclaw.graph.nodes.react_loop.get_llm_service", return_value=FakeLLM()), \
         patch("qrclaw.graph.nodes.react_loop.ToolRunner.run", side_effect=RuntimeError("拒绝执行")), \
         patch.object(ReactLoopNode, "_get_memory_integration", return_value=None):
        ReactLoopNode().run(session, Console(file=StringIO()), workspace, True, is_sub_agent=False)

    restored = Session(workspace.sessions_dir, session.session_id)
    assert [(event["title"], event["content"]) for event in restored.events if event["kind"] == "tool_status"] == [
        ("工具开始", "read_file"), ("工具失败", "read_file")]
    assert "不要直播" not in str(restored.events)
