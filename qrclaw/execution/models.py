"""前后端运行协议使用的领域模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""

    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    """一次任务运行的状态。"""

    QUEUED = "queued"
    ROUTING = "routing"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStatus(StrEnum):
    """Agent 实例状态。"""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    """计划步骤状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolCallStatus(StrEnum):
    """工具调用状态。"""

    PENDING = "pending"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


class EventEnvelope(BaseModel):
    """WebSocket 与持久化共用的事件信封。"""

    version: str = "1"
    event_id: str
    seq: int
    timestamp: datetime
    conversation_id: str
    run_id: str
    agent_id: str | None = None
    parent_agent_id: str | None = None
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class RunModel(BaseModel):
    """任务运行的可查询状态。"""

    run_id: str
    conversation_id: str
    status: RunStatus = RunStatus.QUEUED
    route: str | None = None
    goal: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_seq: int = 0
    error: dict[str, Any] | None = None


class PlanStepModel(BaseModel):
    """计划中的单个步骤。"""

    step_id: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    output: str | None = None


class PlanModel(BaseModel):
    """当前运行计划。"""

    plan_id: str
    goal: str
    project_path: str = ""
    steps: list[PlanStepModel] = Field(default_factory=list)
    revision: int = 1


class AgentModel(BaseModel):
    """主 Agent 或子 Agent 的状态。"""

    agent_id: str
    parent_agent_id: str | None = None
    run_id: str
    step_id: str | None = None
    name: str
    task: str
    status: AgentStatus = AgentStatus.PENDING
    current_action: str = ""
    decision_summary: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: str | None = None
    error: str | None = None


class ToolCallModel(BaseModel):
    """一次工具调用及其结果。"""

    tool_call_id: str
    run_id: str
    agent_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: ToolCallStatus = ToolCallStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: str | None = None
    error: str | None = None


class ApprovalModel(BaseModel):
    """等待用户处理的工具授权。"""

    approval_id: str
    tool_call_id: str
    run_id: str
    agent_id: str
    status: str = "pending"
    decision: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AssistantMessageModel(BaseModel):
    """运行期间生成的助手消息。"""

    message_id: str
    content: str = ""
    completed: bool = False


class RunSnapshot(BaseModel):
    """页面首次加载和断线恢复使用的完整运行快照。"""

    run: RunModel
    plan: PlanModel | None = None
    agents: list[AgentModel] = Field(default_factory=list)
    tool_calls: list[ToolCallModel] = Field(default_factory=list)
    pending_approvals: list[ApprovalModel] = Field(default_factory=list)
    messages: list[AssistantMessageModel] = Field(default_factory=list)
    file_changes: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
