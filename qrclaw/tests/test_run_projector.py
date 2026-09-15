"""运行事件投影与快照恢复测试。"""

from __future__ import annotations

from datetime import datetime, timezone

from qrclaw.execution.models import (
    AgentStatus,
    EventEnvelope,
    RunStatus,
    StepStatus,
    ToolCallStatus,
)
from qrclaw.execution.projector import RunProjector, create_snapshot


class _EventFactory:
    """按递增序号构造同一次运行的测试事件。"""

    def __init__(self) -> None:
        self.seq = 0

    def create(
        self,
        event_type: str,
        data: dict | None = None,
        *,
        agent_id: str | None = "agent_main",
        parent_agent_id: str | None = None,
    ) -> EventEnvelope:
        """创建带稳定运行标识和递增序号的事件。"""

        self.seq += 1
        return EventEnvelope(
            event_id=f"evt_{self.seq}",
            seq=self.seq,
            timestamp=datetime(2026, 8, 18, 8, 0, self.seq, tzinfo=timezone.utc),
            conversation_id="conv_test",
            run_id="run_test",
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            type=event_type,
            data=data or {},
        )


def _projector() -> RunProjector:
    """创建空白运行快照对应的投影器。"""

    return RunProjector(create_snapshot("run_test", "conv_test", "测试任务"))


def test_plan_events_preserve_existing_step_progress() -> None:
    """计划调整时应保留同 ID 步骤的状态和输出。"""

    projector = _projector()
    events = _EventFactory()
    projector.apply(events.create("route.decided", {"route": "plan"}))
    projector.apply(
        events.create(
            "plan.created",
            {
                "plan_id": "plan_1",
                "goal": "完成测试",
                "revision": 1,
                "steps": [
                    {"step_id": "step_1", "description": "分析"},
                    {
                        "step_id": "step_2",
                        "description": "验证",
                        "depends_on": ["step_1"],
                    },
                ],
            },
        )
    )
    projector.apply(events.create("step.started", {"step_id": "step_1"}))
    projector.apply(
        events.create("step.completed", {"step_id": "step_1", "output": "分析完成"})
    )
    projector.apply(
        events.create(
            "plan.updated",
            {
                "plan_id": "plan_1",
                "goal": "完成测试",
                "revision": 2,
                "steps": [
                    {"step_id": "step_1", "description": "分析（已完成）"},
                    {
                        "step_id": "step_2",
                        "description": "验证",
                        "depends_on": ["step_1"],
                    },
                    {
                        "step_id": "step_3",
                        "description": "补充检查",
                        "depends_on": ["step_2"],
                    },
                ],
            },
        )
    )

    snapshot = projector.snapshot
    assert snapshot.run.route == "plan"
    assert snapshot.plan is not None
    assert snapshot.plan.revision == 2
    assert snapshot.plan.steps[0].status == StepStatus.COMPLETED
    assert snapshot.plan.steps[0].output == "分析完成"
    assert snapshot.plan.steps[2].status == StepStatus.PENDING


def test_agent_and_tool_approval_events_build_recoverable_snapshot() -> None:
    """授权前后的 Agent、工具与待处理列表应保持一致。"""

    projector = _projector()
    events = _EventFactory()
    projector.apply(events.create("run.started"))
    projector.apply(
        events.create(
            "agent.started",
            {"name": "依赖检查", "task": "检查锁文件", "step_id": "step_1"},
        )
    )
    projector.apply(
        events.create(
            "tool.approval_required",
            {
                "approval_id": "approval_1",
                "tool_call_id": "tool_1",
                "tool_name": "run_shell",
                "arguments": {"command": "uv lock --check"},
                "purpose": "检查锁文件一致性",
            },
        )
    )

    waiting = projector.snapshot
    assert waiting.run.status == RunStatus.RUNNING
    assert waiting.agents[0].status == AgentStatus.WAITING_APPROVAL
    assert waiting.tool_calls[0].status == ToolCallStatus.WAITING_APPROVAL
    assert waiting.pending_approvals[0].details["purpose"] == "检查锁文件一致性"

    projector.apply(
        events.create(
            "tool.approval_resolved",
            {"approval_id": "approval_1", "decision": "allow_once"},
        )
    )
    projector.apply(
        events.create(
            "tool.started",
            {
                "tool_call_id": "tool_1",
                "name": "run_shell",
                "arguments": {"command": "uv lock --check"},
            },
        )
    )
    projector.apply(
        events.create("tool.completed", {"tool_call_id": "tool_1", "result": "一致"})
    )

    resolved = projector.snapshot
    assert resolved.pending_approvals == []
    assert resolved.agents[0].status == AgentStatus.RUNNING
    assert resolved.tool_calls[0].status == ToolCallStatus.COMPLETED
    assert resolved.tool_calls[0].result == "一致"
    assert resolved.tool_calls[0].started_at is not None
    assert resolved.tool_calls[0].completed_at is not None


def test_assistant_usage_and_file_events_are_accumulated() -> None:
    """流式回复、用量和文件变化应可从事件完整恢复。"""

    projector = _projector()
    events = _EventFactory()
    projector.apply(
        events.create("assistant.delta", {"message_id": "msg_1", "delta": "测试"})
    )
    projector.apply(
        events.create("assistant.delta", {"message_id": "msg_1", "delta": "完成"})
    )
    projector.apply(
        events.create(
            "assistant.completed",
            {"message_id": "msg_1", "content": "测试完成"},
        )
    )
    projector.apply(
        events.create("usage.updated", {"prompt_tokens": 10, "total_tokens": 14})
    )
    projector.apply(events.create("usage.updated", {"duration_ms": 230}))
    projector.apply(
        events.create(
            "file.changed",
            {"path": "/project/pyproject.toml", "change_type": "modified"},
        )
    )

    snapshot = projector.snapshot
    assert snapshot.messages[0].content == "测试完成"
    assert snapshot.messages[0].completed is True
    assert snapshot.usage == {
        "prompt_tokens": 10,
        "total_tokens": 14,
        "duration_ms": 230,
    }
    assert snapshot.file_changes == [
        {"path": "/project/pyproject.toml", "change_type": "modified"}
    ]


def test_duplicate_or_stale_event_is_ignored() -> None:
    """重复或旧序号事件不得覆盖已经投影的新状态。"""

    projector = _projector()
    events = _EventFactory()
    started = events.create("run.started")
    completed = events.create("run.completed")
    projector.apply(started)
    projector.apply(completed)
    projector.apply(started.model_copy(update={"type": "run.failed"}))

    assert projector.snapshot.run.status == RunStatus.COMPLETED
    assert projector.snapshot.run.last_seq == completed.seq
    assert projector.snapshot.run.error is None


def test_failed_run_settles_all_active_resources() -> None:
    """运行失败时应收敛未结束的步骤、Agent、工具和授权。"""

    projector = _projector()
    events = _EventFactory()
    projector.apply(
        events.create(
            "plan.created",
            {
                "plan_id": "plan_1",
                "goal": "失败测试",
                "steps": [
                    {"step_id": "step_1", "description": "运行中"},
                    {"step_id": "step_2", "description": "待执行"},
                ],
            },
        )
    )
    projector.apply(events.create("step.started", {"step_id": "step_1"}))
    projector.apply(
        events.create("agent.started", {"name": "执行 Agent", "task": "执行步骤"})
    )
    projector.apply(
        events.create(
            "tool.approval_required",
            {
                "approval_id": "approval_1",
                "tool_call_id": "tool_1",
                "tool_name": "run_shell",
            },
        )
    )
    projector.apply(
        events.create(
            "run.failed",
            {"code": "run_execution_failed", "message": "执行失败"},
            agent_id=None,
        )
    )

    snapshot = projector.snapshot
    assert snapshot.run.status == RunStatus.FAILED
    assert snapshot.run.error == {
        "code": "run_execution_failed",
        "message": "执行失败",
    }
    assert [step.status for step in snapshot.plan.steps] == [
        StepStatus.FAILED,
        StepStatus.FAILED,
    ]
    assert snapshot.agents[0].status == AgentStatus.FAILED
    assert snapshot.tool_calls[0].status == ToolCallStatus.FAILED
    assert snapshot.pending_approvals == []


def test_cancelled_run_marks_active_resources_cancelled() -> None:
    """运行取消时应将所有活动资源标记为取消。"""

    projector = _projector()
    events = _EventFactory()
    projector.apply(
        events.create("agent.started", {"name": "执行 Agent", "task": "长任务"})
    )
    projector.apply(
        events.create(
            "tool.started",
            {"tool_call_id": "tool_1", "name": "run_shell", "arguments": {}},
        )
    )
    projector.apply(events.create("run.cancelled", {"reason": "user_requested"}))

    snapshot = projector.snapshot
    assert snapshot.run.status == RunStatus.CANCELLED
    assert snapshot.agents[0].status == AgentStatus.CANCELLED
    assert snapshot.tool_calls[0].status == ToolCallStatus.CANCELLED
