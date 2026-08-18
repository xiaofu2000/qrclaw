"""把运行事件归并为可恢复的快照。"""

from __future__ import annotations

from datetime import datetime

from qrclaw.execution.models import (
    AgentModel,
    AgentStatus,
    ApprovalModel,
    AssistantMessageModel,
    EventEnvelope,
    PlanModel,
    PlanStepModel,
    RunModel,
    RunSnapshot,
    RunStatus,
    StepStatus,
    ToolCallModel,
    ToolCallStatus,
)


class RunProjector:
    """线程外部加锁后使用的运行快照投影器。"""

    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot

    def apply(self, event: EventEnvelope) -> RunSnapshot:
        """将单条事件幂等归并到当前快照。"""

        if event.seq <= self.snapshot.run.last_seq:
            return self.snapshot

        self.snapshot.run.last_seq = event.seq
        self.snapshot.run.updated_at = event.timestamp
        handler = getattr(self, f"_on_{event.type.replace('.', '_')}", None)
        if handler:
            handler(event)
        return self.snapshot

    def _agent(self, agent_id: str | None) -> AgentModel | None:
        return next((item for item in self.snapshot.agents if item.agent_id == agent_id), None)

    def _step(self, step_id: str | None) -> PlanStepModel | None:
        if not self.snapshot.plan:
            return None
        return next((item for item in self.snapshot.plan.steps if item.step_id == step_id), None)

    def _tool(self, tool_call_id: str) -> ToolCallModel | None:
        return next(
            (item for item in self.snapshot.tool_calls if item.tool_call_id == tool_call_id),
            None,
        )

    def _on_run_started(self, event: EventEnvelope) -> None:
        self.snapshot.run.status = RunStatus.RUNNING

    def _on_run_status_changed(self, event: EventEnvelope) -> None:
        self.snapshot.run.status = RunStatus(event.data["status"])

    def _on_route_decided(self, event: EventEnvelope) -> None:
        self.snapshot.run.route = event.data["route"]

    def _on_plan_created(self, event: EventEnvelope) -> None:
        self.snapshot.plan = PlanModel.model_validate(event.data)

    def _on_plan_updated(self, event: EventEnvelope) -> None:
        if not self.snapshot.plan:
            self._on_plan_created(event)
            return
        updated = PlanModel.model_validate(event.data)
        current_steps = {step.step_id: step for step in self.snapshot.plan.steps}
        for step in updated.steps:
            previous = current_steps.get(step.step_id)
            if previous:
                step.status = previous.status
                step.output = previous.output
        self.snapshot.plan = updated

    def _on_step_started(self, event: EventEnvelope) -> None:
        step = self._step(event.data.get("step_id"))
        if step:
            step.status = StepStatus.RUNNING

    def _on_step_completed(self, event: EventEnvelope) -> None:
        step = self._step(event.data.get("step_id"))
        if step:
            step.status = StepStatus.COMPLETED
            step.output = event.data.get("output")

    def _on_step_failed(self, event: EventEnvelope) -> None:
        step = self._step(event.data.get("step_id"))
        if step:
            step.status = StepStatus.FAILED
            step.output = event.data.get("error")

    def _on_agent_started(self, event: EventEnvelope) -> None:
        agent = self._agent(event.agent_id)
        if agent:
            agent.status = AgentStatus.RUNNING
            agent.started_at = event.timestamp
            return
        self.snapshot.agents.append(
            AgentModel(
                agent_id=event.agent_id or "",
                parent_agent_id=event.parent_agent_id,
                run_id=event.run_id,
                step_id=event.data.get("step_id"),
                name=event.data.get("name", "Agent"),
                task=event.data.get("task", ""),
                status=AgentStatus.RUNNING,
                started_at=event.timestamp,
            )
        )

    def _on_agent_progress(self, event: EventEnvelope) -> None:
        agent = self._agent(event.agent_id)
        if not agent:
            return
        if "current_action" in event.data:
            agent.current_action = event.data["current_action"]
        if "decision_summary" in event.data:
            agent.decision_summary = event.data["decision_summary"]

    def _finish_agent(self, event: EventEnvelope, status: AgentStatus) -> None:
        agent = self._agent(event.agent_id)
        if agent:
            agent.status = status
            agent.completed_at = event.timestamp
            agent.result = event.data.get("result")
            agent.error = event.data.get("error")

    def _on_agent_completed(self, event: EventEnvelope) -> None:
        self._finish_agent(event, AgentStatus.COMPLETED)

    def _on_agent_failed(self, event: EventEnvelope) -> None:
        self._finish_agent(event, AgentStatus.FAILED)

    def _on_agent_cancelled(self, event: EventEnvelope) -> None:
        self._finish_agent(event, AgentStatus.CANCELLED)

    def _on_tool_approval_required(self, event: EventEnvelope) -> None:
        data = event.data
        tool = self._tool(data["tool_call_id"])
        if not tool:
            tool = ToolCallModel(
                tool_call_id=data["tool_call_id"],
                run_id=event.run_id,
                agent_id=event.agent_id or "",
                name=data["tool_name"],
                arguments=data.get("arguments", {}),
                status=ToolCallStatus.WAITING_APPROVAL,
            )
            self.snapshot.tool_calls.append(tool)
        self.snapshot.pending_approvals.append(
            ApprovalModel(
                approval_id=data["approval_id"],
                tool_call_id=data["tool_call_id"],
                run_id=event.run_id,
                agent_id=event.agent_id or "",
                details=data,
                created_at=event.timestamp,
            )
        )
        agent = self._agent(event.agent_id)
        if agent:
            agent.status = AgentStatus.WAITING_APPROVAL

    def _on_tool_approval_resolved(self, event: EventEnvelope) -> None:
        approval_id = event.data["approval_id"]
        approval = next(
            (item for item in self.snapshot.pending_approvals if item.approval_id == approval_id),
            None,
        )
        if approval:
            approval.status = "resolved"
            approval.decision = event.data["decision"]
            approval.resolved_at = event.timestamp
        self.snapshot.pending_approvals = [
            item for item in self.snapshot.pending_approvals if item.status == "pending"
        ]
        agent = self._agent(event.agent_id)
        if agent and agent.status == AgentStatus.WAITING_APPROVAL:
            agent.status = AgentStatus.RUNNING

    def _on_tool_started(self, event: EventEnvelope) -> None:
        data = event.data
        tool = self._tool(data["tool_call_id"])
        if not tool:
            tool = ToolCallModel(
                tool_call_id=data["tool_call_id"],
                run_id=event.run_id,
                agent_id=event.agent_id or "",
                name=data["name"],
                arguments=data.get("arguments", {}),
            )
            self.snapshot.tool_calls.append(tool)
        tool.status = ToolCallStatus.RUNNING
        tool.started_at = event.timestamp

    def _finish_tool(self, event: EventEnvelope, status: ToolCallStatus) -> None:
        tool = self._tool(event.data["tool_call_id"])
        if tool:
            tool.status = status
            tool.completed_at = event.timestamp
            tool.result = event.data.get("result")
            tool.error = event.data.get("error")

    def _on_tool_completed(self, event: EventEnvelope) -> None:
        self._finish_tool(event, ToolCallStatus.COMPLETED)

    def _on_tool_failed(self, event: EventEnvelope) -> None:
        self._finish_tool(event, ToolCallStatus.FAILED)

    def _on_tool_denied(self, event: EventEnvelope) -> None:
        self._finish_tool(event, ToolCallStatus.DENIED)

    def _on_assistant_delta(self, event: EventEnvelope) -> None:
        message_id = event.data["message_id"]
        message = next(
            (item for item in self.snapshot.messages if item.message_id == message_id),
            None,
        )
        if not message:
            message = AssistantMessageModel(message_id=message_id)
            self.snapshot.messages.append(message)
        message.content += event.data.get("delta", "")

    def _on_assistant_completed(self, event: EventEnvelope) -> None:
        message_id = event.data["message_id"]
        message = next(
            (item for item in self.snapshot.messages if item.message_id == message_id),
            None,
        )
        if not message:
            message = AssistantMessageModel(
                message_id=message_id,
                content=event.data.get("content", ""),
            )
            self.snapshot.messages.append(message)
        message.completed = True

    def _on_usage_updated(self, event: EventEnvelope) -> None:
        self.snapshot.usage.update(event.data)

    def _on_file_changed(self, event: EventEnvelope) -> None:
        self.snapshot.file_changes.append(event.data)

    def _finish_run(self, event: EventEnvelope, status: RunStatus) -> None:
        self.snapshot.run.status = status
        if status == RunStatus.FAILED:
            self.snapshot.run.error = event.data
        if status in {RunStatus.FAILED, RunStatus.CANCELLED}:
            agent_status = (
                AgentStatus.FAILED if status == RunStatus.FAILED else AgentStatus.CANCELLED
            )
            step_status = StepStatus.FAILED if status == RunStatus.FAILED else StepStatus.CANCELLED
            tool_status = (
                ToolCallStatus.FAILED if status == RunStatus.FAILED else ToolCallStatus.CANCELLED
            )
            for agent in self.snapshot.agents:
                if agent.status in {
                    AgentStatus.PENDING,
                    AgentStatus.RUNNING,
                    AgentStatus.WAITING_APPROVAL,
                }:
                    agent.status = agent_status
                    agent.completed_at = event.timestamp
            if self.snapshot.plan:
                for step in self.snapshot.plan.steps:
                    if step.status in {StepStatus.PENDING, StepStatus.RUNNING}:
                        step.status = step_status
            for tool in self.snapshot.tool_calls:
                if tool.status in {
                    ToolCallStatus.PENDING,
                    ToolCallStatus.WAITING_APPROVAL,
                    ToolCallStatus.RUNNING,
                }:
                    tool.status = tool_status
                    tool.completed_at = event.timestamp
            self.snapshot.pending_approvals = []

    def _on_run_completed(self, event: EventEnvelope) -> None:
        self._finish_run(event, RunStatus.COMPLETED)

    def _on_run_failed(self, event: EventEnvelope) -> None:
        self._finish_run(event, RunStatus.FAILED)

    def _on_run_cancelled(self, event: EventEnvelope) -> None:
        self._finish_run(event, RunStatus.CANCELLED)


def create_snapshot(run_id: str, conversation_id: str, goal: str) -> RunSnapshot:
    """创建尚未开始执行的初始快照。"""

    return RunSnapshot(
        run=RunModel(run_id=run_id, conversation_id=conversation_id, goal=goal)
    )
