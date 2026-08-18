"""工具结构化事件与授权测试。"""

from __future__ import annotations

from qrclaw.execution.context import CancellationToken, ExecutionContext
from qrclaw.graph.nodes import tool_runner as tool_runner_module
from qrclaw.graph.nodes.tool_runner import ToolRunner


class _ApprovalProvider:
    """测试用授权器。"""

    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def request(self, context, **kwargs) -> bool:
        context.publish(
            "tool.approval_required",
            {
                "approval_id": kwargs["approval_id"],
                "tool_call_id": kwargs["tool_call_id"],
                "tool_name": kwargs["tool_name"],
                "arguments": kwargs["arguments"],
            },
        )
        return self.allowed


def _context(events, provider) -> ExecutionContext:
    return ExecutionContext(
        conversation_id="conv_test",
        run_id="run_test",
        agent_id="agent_test",
        agent_name="测试 Agent",
        task="测试工具",
        event_sink=lambda event_type, data, _: events.append((event_type, data)),
        cancellation=CancellationToken(),
        approval_provider=provider,
    )


def test_tool_events_are_attributed_to_agent(monkeypatch):
    """普通工具应产生开始和完成事件。"""

    events = []
    monkeypatch.setattr(tool_runner_module, "need_confirm", lambda _: False)
    monkeypatch.setattr(tool_runner_module, "execute", lambda _name, _arguments: "成功")

    result = ToolRunner(execution_context=_context(events, None)).run("read_file", "{\"path\":\"x\"}")

    assert result == "成功"
    assert [event[0] for event in events] == ["tool.started", "tool.completed"]
    assert events[0][1]["tool_call_id"] == events[1][1]["tool_call_id"]


def test_denied_tool_is_not_executed(monkeypatch):
    """拒绝授权后不得执行危险工具。"""

    events = []
    executed = []
    monkeypatch.setattr(tool_runner_module, "need_confirm", lambda _: True)
    monkeypatch.setattr(
        tool_runner_module,
        "execute",
        lambda _name, _arguments: executed.append(True) or "不应执行",
    )

    result = ToolRunner(
        execution_context=_context(events, _ApprovalProvider(allowed=False))
    ).run("run_shell", "{\"command\":\"echo test\"}")

    assert result == "用户拒绝执行此操作"
    assert executed == []
    assert [event[0] for event in events] == ["tool.approval_required", "tool.denied"]
