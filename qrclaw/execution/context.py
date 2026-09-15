"""将事件、授权与取消能力注入 Agent 执行链。"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol


class RunCancelled(RuntimeError):
    """运行收到取消请求后用于中断执行链的异常。"""


class CancellationToken:
    """可在线程间安全共享的协作式取消令牌。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        """返回是否已经请求取消。"""

        return self._event.is_set()

    def cancel(self) -> None:
        """请求取消当前运行。"""

        self._event.set()

    def raise_if_cancelled(self) -> None:
        """若已取消则立即中断当前执行路径。"""

        if self.is_cancelled:
            raise RunCancelled("用户已取消任务")


class ApprovalProvider(Protocol):
    """危险工具授权提供器接口。"""

    def request(
        self,
        context: "ExecutionContext",
        *,
        approval_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> bool:
        """等待一次授权决定，允许返回 True，拒绝返回 False。"""


EventSink = Callable[[str, dict[str, Any], "ExecutionContext"], None]


@dataclass(slots=True)
class ExecutionContext:
    """一次 Agent 执行路径的显式上下文。"""

    conversation_id: str
    run_id: str
    agent_id: str
    agent_name: str
    task: str
    event_sink: EventSink
    cancellation: CancellationToken
    approval_provider: ApprovalProvider | None = None
    workspace_path: str = ""
    sandbox_profile: str = "default"
    parent_agent_id: str | None = None
    step_id: str | None = None
    plan_id: str | None = None
    assistant_message_id: str | None = None
    assistant_completed: bool = False

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """发布一条归属于当前 Agent 的结构化事件。"""

        self.event_sink(event_type, data or {}, self)

    def check_cancelled(self) -> None:
        """检查并抛出取消异常。"""

        self.cancellation.raise_if_cancelled()

    def child_agent(self, name: str, task: str, step_id: str | None = None) -> "ExecutionContext":
        """创建具有唯一实例 ID 的子 Agent 上下文。"""

        return replace(
            self,
            agent_id=f"agent_{uuid.uuid4().hex}",
            agent_name=name,
            task=task,
            parent_agent_id=self.agent_id,
            step_id=step_id,
            sandbox_profile=name,
            assistant_message_id=None,
            assistant_completed=False,
        )

    @staticmethod
    def new_id(prefix: str) -> str:
        """生成协议使用的不透明 ID。"""

        return f"{prefix}_{uuid.uuid4().hex}"
