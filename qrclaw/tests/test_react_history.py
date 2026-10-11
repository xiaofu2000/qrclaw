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
        def chat(self, *_args, **_kwargs):
            return next(responses)

    with patch("qrclaw.graph.nodes.react_loop.get_llm_service", return_value=FakeLLM()), \
         patch("qrclaw.graph.nodes.react_loop.ToolRunner.run", return_value="文件内容"):
        result = ReactLoopNode().run(session, Console(file=StringIO()), workspace, True, is_sub_agent=True)

    restored = Session(workspace.sessions_dir, session.session_id)
    assert result == "完成"
    assert [message["role"] for message in restored.messages] == ["user", "assistant", "tool", "assistant"]
    assert restored.messages[1]["tool_calls"][0]["id"] == "call-1"
    assert restored.messages[2]["tool_call_id"] == "call-1"
