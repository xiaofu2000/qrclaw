"""运行服务、事件持久化和恢复测试。"""

from __future__ import annotations

import time

from qrclaw.execution.models import RunStatus
from qrclaw.execution.service import RunService
from qrclaw.graph.nodes import tool_runner as tool_runner_module
from qrclaw.graph.nodes.tool_runner import ToolRunner
from qrclaw.workspace import Workspace


def _wait_terminal(service: RunService, run_id: str):
    """等待测试任务进入终态。"""

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        snapshot = service.get_snapshot(run_id)
        if snapshot.run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("测试任务未在限定时间内结束")


def test_run_service_persists_events_and_snapshot(tmp_path):
    """任务完成后应同时保存事件和完整快照。"""

    def fake_runner(**kwargs):
        context = kwargs["execution_context"]
        context.publish("route.decided", {"route": "direct"})
        context.publish("agent.progress", {"current_action": "正在执行测试任务"})
        return "测试完成"

    workspace = Workspace(agent_id="test", _root=tmp_path / "agent")
    service = RunService(
        workspace=workspace,
        database_path=tmp_path / "runtime.sqlite3",
        agent_runner=fake_runner,
    )
    conversation = service.create_conversation("测试会话", str(tmp_path))
    snapshot = service.start_run(
        conversation["conversation_id"],
        "执行测试",
        "client-1",
    )
    completed = _wait_terminal(service, snapshot.run.run_id)

    assert completed.run.status == RunStatus.COMPLETED
    assert completed.run.route == "direct"
    assert completed.messages[0].content == "测试完成"
    assert completed.agents[0].current_action == "正在执行测试任务"
    events = service.get_events(snapshot.run.run_id, 0)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert events[-1].type == "run.completed"


def test_client_request_id_is_idempotent(tmp_path):
    """重复创建请求不能启动第二个任务。"""

    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs["user_input"])
        return "完成"

    workspace = Workspace(agent_id="test", _root=tmp_path / "agent")
    service = RunService(
        workspace=workspace,
        database_path=tmp_path / "runtime.sqlite3",
        agent_runner=fake_runner,
    )
    conversation = service.create_conversation("测试会话", str(tmp_path))
    first = service.start_run(conversation["conversation_id"], "第一次", "same-request")
    second = service.start_run(conversation["conversation_id"], "第二次", "same-request")
    _wait_terminal(service, first.run.run_id)

    assert first.run.run_id == second.run.run_id
    assert calls == ["第一次"]


def test_service_restart_marks_incomplete_run_failed(tmp_path):
    """服务重启后不能留下永久运行中的快照。"""

    workspace = Workspace(agent_id="test", _root=tmp_path / "agent")
    database = tmp_path / "runtime.sqlite3"
    first_service = RunService(
        workspace=workspace,
        database_path=database,
        agent_runner=lambda **_: "完成",
    )
    conversation = first_service.create_conversation("测试会话", str(tmp_path))
    from qrclaw.execution.projector import create_snapshot

    running = create_snapshot("run_interrupted", conversation["conversation_id"], "中断任务")
    running.run.status = RunStatus.RUNNING
    first_service.store.create_run(running, "blocked")

    restarted = RunService(
        workspace=workspace,
        database_path=database,
        agent_runner=lambda **_: "完成",
    )
    recovered = restarted.get_snapshot(running.run.run_id)

    assert recovered.run.status == RunStatus.FAILED
    assert recovered.run.error["code"] == "service_restarted"


def test_runtime_approval_resumes_tool(tmp_path, monkeypatch):
    """HTTP 授权提供器允许后，等待中的工具应继续执行。"""

    monkeypatch.setattr(tool_runner_module, "need_confirm", lambda _: True)
    monkeypatch.setattr(tool_runner_module, "execute", lambda _name, _arguments: "已执行")

    def fake_runner(**kwargs):
        return ToolRunner(execution_context=kwargs["execution_context"]).run(
            "run_shell",
            '{"command":"echo test"}',
        )

    workspace = Workspace(agent_id="test", _root=tmp_path / "agent")
    service = RunService(
        workspace=workspace,
        database_path=tmp_path / "runtime.sqlite3",
        agent_runner=fake_runner,
    )
    conversation = service.create_conversation("授权测试", str(tmp_path))
    run = service.start_run(conversation["conversation_id"], "执行命令", "approval")

    deadline = time.monotonic() + 3
    approval = None
    while time.monotonic() < deadline:
        pending = service.get_snapshot(run.run.run_id).pending_approvals
        if pending:
            approval = pending[0]
            break
        time.sleep(0.01)
    assert approval is not None
    assert service.get_snapshot(run.run.run_id).run.status == RunStatus.WAITING_APPROVAL

    service.resolve_approval(approval.approval_id, "allow_once")
    completed = _wait_terminal(service, run.run.run_id)

    assert completed.run.status == RunStatus.COMPLETED
    assert completed.tool_calls[0].status.value == "completed"
    event_types = [event.type for event in service.get_events(run.run.run_id, 0)]
    assert "tool.approval_required" in event_types
    assert "tool.approval_resolved" in event_types


def test_cancel_request_reaches_execution_context(tmp_path):
    """取消请求应由执行线程确认并停止后续动作。"""

    def fake_runner(**kwargs):
        context = kwargs["execution_context"]
        while True:
            context.check_cancelled()
            time.sleep(0.01)

    workspace = Workspace(agent_id="test", _root=tmp_path / "agent")
    service = RunService(
        workspace=workspace,
        database_path=tmp_path / "runtime.sqlite3",
        agent_runner=fake_runner,
    )
    conversation = service.create_conversation("取消测试", str(tmp_path))
    run = service.start_run(conversation["conversation_id"], "持续运行", "cancel")

    service.cancel_run(run.run.run_id)
    cancelled = _wait_terminal(service, run.run.run_id)

    assert cancelled.run.status == RunStatus.CANCELLED
    event_types = [event.type for event in service.get_events(run.run.run_id, 0)]
    assert "run.status_changed" in event_types
    assert event_types[-1] == "run.cancelled"
