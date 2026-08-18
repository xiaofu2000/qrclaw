"""统一管理任务启动、事件、授权、取消与快照。"""

from __future__ import annotations

import threading
import uuid
import os
import secrets
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from qrclaw.execution.context import CancellationToken, ExecutionContext, RunCancelled
from qrclaw.execution.models import EventEnvelope, RunSnapshot, RunStatus, utc_now
from qrclaw.execution.projector import RunProjector, create_snapshot
from qrclaw.execution.store import RuntimeStore
from qrclaw.logger import get_logger
from qrclaw.memory.context.session import Session
from qrclaw.workspace import Workspace

logger = get_logger("qrclaw.execution.service")


@dataclass(slots=True)
class _RunHandle:
    """运行中的内存协调对象。"""

    projector: RunProjector
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    lock: threading.RLock = field(default_factory=threading.RLock)
    changed: threading.Condition = field(init=False)
    thread: threading.Thread | None = None

    def __post_init__(self) -> None:
        self.changed = threading.Condition(self.lock)


@dataclass(slots=True)
class _PendingApproval:
    """一次等待中的授权。"""

    context: ExecutionContext
    event: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None


class RuntimeApprovalProvider:
    """通过运行服务等待 HTTP 授权决定。"""

    def __init__(self, service: "RunService") -> None:
        self._service = service

    def request(
        self,
        context: ExecutionContext,
        *,
        approval_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> bool:
        from qrclaw.sandbox import is_sandbox_enabled

        pending = _PendingApproval(context=context)
        with self._service._approval_lock:
            self._service._pending_approvals[approval_id] = pending

        context.publish(
            "tool.approval_required",
            {
                "approval_id": approval_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "purpose": f"执行工具 {tool_name}",
                "arguments": arguments,
                "cwd": context.workspace_path or str(Path.cwd()),
                "sandbox": {"enabled": is_sandbox_enabled(context.sandbox_profile)},
                "risk_level": "high",
            },
        )
        self._service._refresh_waiting_status(context.run_id)
        logger.info("工具等待用户授权：%s", tool_name)

        while not pending.event.wait(timeout=0.2):
            context.check_cancelled()

        context.check_cancelled()
        return pending.decision == "allow_once"


class RunService:
    """前端和 CLI 共用的任务运行服务。"""

    def __init__(
        self,
        workspace: Workspace | None = None,
        database_path: Path | None = None,
        agent_runner: Callable[..., str] | None = None,
        access_token: str | None = None,
    ) -> None:
        self.workspace = workspace or Workspace(agent_id="default")
        self.access_token = access_token or os.environ.get("QRCLAW_ACCESS_TOKEN") or secrets.token_urlsafe(32)
        self.access_token_path = self.workspace.root / "local_access_token"
        self.access_token_path.write_text(self.access_token, encoding="utf-8")
        self.access_token_path.chmod(0o600)
        self.store = RuntimeStore(database_path or self.workspace.root / "runtime.sqlite3")
        self._handles: dict[str, _RunHandle] = {}
        self._handles_lock = threading.RLock()
        self._approval_lock = threading.RLock()
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._resolved_approvals: dict[str, str] = {}
        self.approval_provider = RuntimeApprovalProvider(self)
        self._agent_runner = agent_runner
        self._restore_snapshots()

    def _restore_snapshots(self) -> None:
        """加载历史快照，并将异常退出的运行收敛为失败。"""

        incomplete = {
            RunStatus.QUEUED,
            RunStatus.ROUTING,
            RunStatus.RUNNING,
            RunStatus.WAITING_APPROVAL,
            RunStatus.CANCELLING,
        }
        for snapshot in self.store.list_snapshots():
            projector = RunProjector(snapshot)
            for event in self.store.events_after(snapshot.run.run_id, snapshot.run.last_seq):
                projector.apply(event)
            handle = _RunHandle(projector=projector)
            self._handles[snapshot.run.run_id] = handle
            self.store.save_snapshot(projector.snapshot)
            for event in self.store.events_after(snapshot.run.run_id, 0):
                if event.type == "tool.approval_resolved":
                    self._resolved_approvals[event.data["approval_id"]] = event.data["decision"]
            if projector.snapshot.run.status in incomplete:
                self._publish(
                    projector.snapshot.run.run_id,
                    "run.failed",
                    {"code": "service_restarted", "message": "本地服务重启，运行已终止"},
                    None,
                )

    def create_conversation(self, title: str, workspace_path: str) -> dict[str, Any]:
        """创建一个可运行任务的会话。"""

        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError("工作区路径不存在或不是目录")
        normalized = str(workspace)
        return self.store.create_conversation(title=title, workspace_path=normalized)

    def list_conversations(self) -> list[dict[str, Any]]:
        """返回全部会话。"""

        return self.store.list_conversations()

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        """返回指定会话。"""

        return self.store.get_conversation(conversation_id)

    def update_conversation(self, conversation_id: str, title: str) -> dict[str, Any]:
        """修改会话标题。"""

        return self.store.update_conversation(conversation_id, title)

    def delete_conversation(self, conversation_id: str) -> None:
        """删除会话元数据和运行记录，不删除工作区文件。"""

        active = [
            handle.projector.snapshot.run
            for handle in self._handles.values()
            if handle.projector.snapshot.run.conversation_id == conversation_id
            and handle.projector.snapshot.run.status
            in {
                RunStatus.QUEUED,
                RunStatus.ROUTING,
                RunStatus.RUNNING,
                RunStatus.WAITING_APPROVAL,
                RunStatus.CANCELLING,
            }
        ]
        if active:
            raise RuntimeError("会话仍有运行中的任务，不能删除")
        self.store.delete_conversation(conversation_id)
        session_path = self.workspace.sessions_dir / f"{conversation_id}.json"
        session_path.unlink(missing_ok=True)
        with self._handles_lock:
            self._handles = {
                run_id: handle
                for run_id, handle in self._handles.items()
                if handle.projector.snapshot.run.conversation_id != conversation_id
            }

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """读取 Agent 会话文件中的消息。"""

        self.store.get_conversation(conversation_id)
        session = Session(
            sessions_dir=self.workspace.sessions_dir,
            session_id=conversation_id,
            resume=True,
        )
        messages = []
        for index, message in enumerate(session.messages):
            item = dict(message)
            item["message_id"] = item.pop(
                "uuid",
                uuid.uuid5(uuid.NAMESPACE_URL, f"{conversation_id}:{index}").hex,
            )
            messages.append(item)
        return messages

    def start_run(self, conversation_id: str, content: str, client_request_id: str) -> RunSnapshot:
        """幂等创建并异步启动任务。"""

        self.store.get_conversation(conversation_id)
        existing_id = self.store.find_run_by_client_request(client_request_id)
        if existing_id:
            return self.get_snapshot(existing_id)

        run_id = f"run_{uuid.uuid4().hex}"
        snapshot = create_snapshot(run_id, conversation_id, content)
        self.store.create_run(snapshot, client_request_id)
        handle = _RunHandle(projector=RunProjector(snapshot))
        with self._handles_lock:
            self._handles[run_id] = handle

        thread = threading.Thread(
            target=self._execute_run,
            args=(run_id, content),
            name=f"qrclaw-{run_id}",
            daemon=True,
        )
        handle.thread = thread
        thread.start()
        logger.info("任务已创建：%s", run_id)
        return self.get_snapshot(run_id)

    def _execute_run(self, run_id: str, content: str) -> None:
        """在线程中执行现有 Agent 图，并把过程投影为事件。"""

        handle = self._handle(run_id)
        snapshot = handle.projector.snapshot
        conversation = self.store.get_conversation(snapshot.run.conversation_id)
        root_context = ExecutionContext(
            conversation_id=snapshot.run.conversation_id,
            run_id=run_id,
            agent_id=f"agent_{uuid.uuid4().hex}",
            agent_name="主 Agent",
            task=content,
            event_sink=self._event_sink,
            cancellation=handle.cancellation,
            approval_provider=self.approval_provider,
            workspace_path=conversation["workspace_path"],
        )
        self._publish(run_id, "run.started", {}, root_context)
        root_context.publish(
            "agent.started",
            {"name": root_context.agent_name, "task": content, "step_id": None},
        )

        try:
            import qrclaw.tools  # noqa: F401
            if self._agent_runner is None:
                from qrclaw.agent import run as run_agent
            else:
                run_agent = self._agent_runner

            session = Session(
                sessions_dir=self.workspace.sessions_dir,
                session_id=snapshot.run.conversation_id,
                resume=True,
            )
            console = Console(file=StringIO(), force_terminal=False, highlight=False)
            result = run_agent(
                user_input=content,
                session=session,
                console=console,
                workspace=self.workspace,
                auto_confirm=False,
                execution_context=root_context,
            )
            root_context.check_cancelled()
            message_id = f"msg_{uuid.uuid4().hex}"
            root_context.publish("assistant.delta", {"message_id": message_id, "delta": result or ""})
            root_context.publish(
                "assistant.completed",
                {"message_id": message_id, "content": result or ""},
            )
            root_context.publish("agent.completed", {"result": result or ""})
            root_context.publish("run.completed", {})
            logger.info("任务执行完成：%s", run_id)
        except RunCancelled:
            root_context.publish("agent.cancelled", {"error": "用户取消任务"})
            root_context.publish("run.cancelled", {"reason": "user_requested"})
            logger.info("任务已取消：%s", run_id)
        except Exception as exc:
            logger.error("任务执行失败：%s，错误：%s", run_id, exc, exc_info=True)
            root_context.publish("agent.failed", {"error": str(exc)})
            root_context.publish(
                "run.failed",
                {"code": "run_execution_failed", "message": str(exc)},
            )
        finally:
            self._release_run_approvals(run_id)

    def _event_sink(
        self,
        event_type: str,
        data: dict[str, Any],
        context: ExecutionContext,
    ) -> None:
        self._publish(context.run_id, event_type, data, context)

    def _publish(
        self,
        run_id: str,
        event_type: str,
        data: dict[str, Any],
        context: ExecutionContext | None,
    ) -> EventEnvelope:
        handle = self._handle(run_id)
        with handle.changed:
            snapshot = handle.projector.snapshot
            event = EventEnvelope(
                event_id=f"evt_{uuid.uuid4().hex}",
                seq=snapshot.run.last_seq + 1,
                timestamp=utc_now(),
                conversation_id=snapshot.run.conversation_id,
                run_id=run_id,
                agent_id=context.agent_id if context else None,
                parent_agent_id=context.parent_agent_id if context else None,
                type=event_type,
                data=data,
            )
            self.store.append_event(event)
            handle.projector.apply(event)
            self.store.save_snapshot(handle.projector.snapshot)
            handle.changed.notify_all()
        return event

    def get_snapshot(self, run_id: str) -> RunSnapshot:
        """返回运行快照的独立副本。"""

        handle = self._handle(run_id)
        with handle.lock:
            return handle.projector.snapshot.model_copy(deep=True)

    def get_events(self, run_id: str, after_seq: int) -> list[EventEnvelope]:
        """读取指定序号之后的运行事件。"""

        self._handle(run_id)
        return self.store.events_after(run_id, after_seq)

    def wait_for_events(self, run_id: str, after_seq: int, timeout: float = 15.0) -> list[EventEnvelope]:
        """等待新事件或超时，供 WebSocket 桥接线程事件。"""

        handle = self._handle(run_id)
        events = self.get_events(run_id, after_seq)
        if events:
            return events
        with handle.changed:
            handle.changed.wait(timeout=timeout)
        return self.get_events(run_id, after_seq)

    def cancel_run(self, run_id: str) -> RunSnapshot:
        """请求取消任务，最终状态由执行线程确认。"""

        handle = self._handle(run_id)
        with handle.lock:
            status = handle.projector.snapshot.run.status
        if status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return self.get_snapshot(run_id)
        self._publish(run_id, "run.status_changed", {"status": "cancelling"}, None)
        handle.cancellation.cancel()
        self._release_run_approvals(run_id)
        return self.get_snapshot(run_id)

    def resolve_approval(self, approval_id: str, decision: str) -> dict[str, str]:
        """幂等处理一次工具授权。"""

        if decision not in {"allow_once", "deny"}:
            raise ValueError("不支持的授权决定")
        with self._approval_lock:
            resolved = self._resolved_approvals.get(approval_id)
            if resolved:
                return {"approval_id": approval_id, "decision": resolved}
            pending = self._pending_approvals.get(approval_id)
            if not pending:
                raise KeyError(approval_id)
            pending.decision = decision
            self._resolved_approvals[approval_id] = decision
            pending.context.publish(
                "tool.approval_resolved",
                {"approval_id": approval_id, "decision": decision},
            )
            self._refresh_waiting_status(pending.context.run_id)
            pending.event.set()
            del self._pending_approvals[approval_id]
        logger.info("工具授权已处理：%s，决定：%s", approval_id, decision)
        return {"approval_id": approval_id, "decision": decision}

    def _refresh_waiting_status(self, run_id: str) -> None:
        """仅在所有可执行叶子 Agent 均被授权阻塞时切换 Run 状态。"""

        snapshot = self.get_snapshot(run_id)
        active = [
            agent
            for agent in snapshot.agents
            if agent.status.value in {"running", "waiting_approval"}
        ]
        active_parent_ids = {
            agent.parent_agent_id for agent in active if agent.parent_agent_id is not None
        }
        leaves = [agent for agent in active if agent.agent_id not in active_parent_ids]
        all_waiting = bool(leaves) and all(
            agent.status.value == "waiting_approval" for agent in leaves
        )
        if all_waiting and snapshot.run.status == RunStatus.RUNNING:
            self._publish(run_id, "run.status_changed", {"status": "waiting_approval"}, None)
        elif not all_waiting and snapshot.run.status == RunStatus.WAITING_APPROVAL:
            self._publish(run_id, "run.status_changed", {"status": "running"}, None)

    def _release_run_approvals(self, run_id: str) -> None:
        """唤醒运行结束后仍在等待的授权。"""

        with self._approval_lock:
            for approval_id, pending in list(self._pending_approvals.items()):
                if pending.context.run_id != run_id:
                    continue
                pending.decision = "deny"
                pending.event.set()
                del self._pending_approvals[approval_id]

    def _handle(self, run_id: str) -> _RunHandle:
        with self._handles_lock:
            handle = self._handles.get(run_id)
        if not handle:
            raise KeyError(run_id)
        return handle
