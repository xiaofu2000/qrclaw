"""工具结构化事件与授权测试。"""

from __future__ import annotations

import pytest

from qrclaw.execution.context import (
    CancellationToken,
    ExecutionContext,
    RunCancelled,
)
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


def _context(events, provider, workspace_path="") -> ExecutionContext:
    """创建记录事件的工具执行上下文。"""

    return ExecutionContext(
        conversation_id="conv_test",
        run_id="run_test",
        agent_id="agent_test",
        agent_name="测试 Agent",
        task="测试工具",
        event_sink=lambda event_type, data, _: events.append((event_type, data)),
        cancellation=CancellationToken(),
        approval_provider=provider,
        workspace_path=workspace_path,
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


@pytest.mark.parametrize(
    ("initial_content", "final_content", "expected_change"),
    [
        (None, "新文件", "created"),
        ("旧内容", "新内容", "modified"),
        ("待删除", None, "deleted"),
    ],
)
def test_tool_reports_explicit_file_changes(
    tmp_path,
    monkeypatch,
    initial_content,
    final_content,
    expected_change,
):
    """工具执行后应根据指纹报告显式文件参数的创建、修改和删除。"""

    target = tmp_path / f"{expected_change}.txt"
    if initial_content is not None:
        target.write_text(initial_content, encoding="utf-8")

    def fake_execute(_name, _arguments):
        if final_content is None:
            target.unlink()
        else:
            target.write_text(final_content, encoding="utf-8")
        return "文件操作完成"

    events = []
    monkeypatch.setattr(tool_runner_module, "need_confirm", lambda _: False)
    monkeypatch.setattr(tool_runner_module, "execute", fake_execute)

    result = ToolRunner(
        execution_context=_context(events, None, workspace_path=str(tmp_path))
    ).run("write_file", f'{{"path":"{target.name}"}}')

    assert result == "文件操作完成"
    assert [event[0] for event in events] == [
        "tool.started",
        "tool.completed",
        "file.changed",
    ]
    assert events[-1][1] == {
        "path": str(target),
        "change_type": expected_change,
    }


def test_tool_failure_publishes_structured_error(monkeypatch):
    """工具异常应转换为失败事件和可返回给 Agent 的错误文本。"""

    events = []

    def fail_tool(*_):
        """模拟普通工具执行异常。"""

        raise RuntimeError("模拟工具失败")

    monkeypatch.setattr(tool_runner_module, "need_confirm", lambda _: False)
    monkeypatch.setattr(tool_runner_module, "execute", fail_tool)

    result = ToolRunner(execution_context=_context(events, None)).run("read_file", "{}")

    assert result == "工具执行失败: 模拟工具失败"
    assert [event[0] for event in events] == ["tool.started", "tool.failed"]
    assert events[-1][1]["error"] == "模拟工具失败"


def test_repeated_permission_error_terminates_run(monkeypatch):
    """连续权限拒绝达到阈值时应终止当前任务，避免无效重试。"""

    events = []

    def deny_tool(*_):
        """模拟操作系统拒绝文件访问。"""

        raise PermissionError("无权限")

    monkeypatch.setattr(tool_runner_module, "need_confirm", lambda _: False)
    monkeypatch.setattr(tool_runner_module, "execute", deny_tool)
    runner = ToolRunner(execution_context=_context(events, None), max_permission_denied=2)

    assert runner.run("read_file", "{}") == "无权限"
    with pytest.raises(RuntimeError, match="连续 2 次权限拒绝"):
        runner.run("read_file", "{}")

    assert [event[0] for event in events] == [
        "tool.started",
        "tool.failed",
        "tool.started",
        "tool.failed",
    ]


def test_cancel_after_tool_execution_publishes_failure_and_propagates(monkeypatch):
    """工具执行期间收到取消后应记录失败，并继续向运行服务传播取消。"""

    events = []
    context = _context(events, None)

    def cancel_during_execute(_name, _arguments):
        context.cancellation.cancel()
        return "不应作为成功结果"

    monkeypatch.setattr(tool_runner_module, "need_confirm", lambda _: False)
    monkeypatch.setattr(tool_runner_module, "execute", cancel_during_execute)

    with pytest.raises(RunCancelled):
        ToolRunner(execution_context=context).run("run_shell", "{}")

    assert [event[0] for event in events] == ["tool.started", "tool.failed"]
    assert events[-1][1]["error"] == "用户取消任务"
