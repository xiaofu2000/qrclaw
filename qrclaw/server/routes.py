from __future__ import annotations

import time
import uuid
import json
from queue import Empty, Full, Queue
from threading import Event, Thread
from threading import Lock, RLock
from io import StringIO
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from rich.console import Console

from qrclaw.agent import run as run_agent
from qrclaw.subagent_progress import has_active_sub_agents, sub_agent_snapshot
from qrclaw.workspace import Workspace

from .schemas import (
    ChatCompletionRequest,
    ChatMessage,
    CreateSessionRequest,
    RenameSessionRequest,
    SessionDetail,
    SessionSummary,
)
from .session_service import SessionService

MODEL_ID = "qrclaw-agent"
_agent_run_lock = Lock()


def build_router(workspace: Workspace) -> APIRouter:
    router = APIRouter()
    service = SessionService(workspace)
    runs: dict[str, str] = {}
    failures: dict[str, str] = {}
    status_lock = RLock()

    def with_status(detail: dict[str, Any]) -> dict[str, Any]:
        with status_lock:
            running_child = sub_agent_snapshot(detail["id"], workspace.sessions_dir) is not None
            return {**detail, "run_status": runs.get(detail["id"], "running" if running_child else "idle"),
                    "active_subagents": has_active_sub_agents(detail["id"], workspace.sessions_dir),
                    "run_error": failures.get(detail["id"])}

    def ensure_session_idle(session_id: str) -> None:
        """修改会话历史前确认主任务和子任务都已结束。"""
        if session_id in runs or sub_agent_snapshot(session_id, workspace.sessions_dir) or has_active_sub_agents(session_id, workspace.sessions_dir):
            raise HTTPException(status_code=409, detail="会话仍在执行任务，暂时不能修改")

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "qrclaw-local-api"}

    @router.get("/v1/models")
    def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "qrclaw",
                }
            ],
        }

    @router.get("/v1/sessions", response_model=list[SessionSummary])
    def list_session_api() -> list[dict[str, Any]]:
        return [with_status(summary) for summary in service.list_sessions()]

    @router.post("/v1/sessions", response_model=SessionDetail)
    def create_session_api(request: CreateSessionRequest | None = Body(default=None)) -> dict[str, Any]:
        with status_lock:
            if request and request.session_id in runs:
                raise HTTPException(status_code=409, detail="会话仍在执行任务")
            session = service.create_session(request.session_id if request else None)
        return with_status(service.describe_session(session))

    @router.get("/v1/sessions/{session_id}", response_model=SessionDetail)
    def get_session_api(session_id: str) -> dict[str, Any]:
        # 避免返回旧内容和刚完成的 idle 状态，让页面误以为结果已完整。
        with status_lock:
            session = service.get_session(session_id)
            if not session._path.exists():
                raise HTTPException(status_code=404, detail="会话不存在")
            return with_status(service.describe_session(session))

    @router.patch("/v1/sessions/{session_id}", response_model=SessionDetail)
    def rename_session_api(session_id: str, request: RenameSessionRequest) -> dict[str, Any]:
        """修改会话标题，不影响消息和执行过程。"""
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="会话标题不能为空")
        with status_lock:
            session = service.get_session(session_id)
            try:
                session.set_title(title)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="会话不存在") from exc
            return with_status(service.describe_session(session))

    @router.delete("/v1/sessions/{session_id}/messages/{message_id}", response_model=SessionDetail)
    def delete_latest_user_message_api(session_id: str, message_id: str) -> dict[str, Any]:
        """删除最后一轮用户消息及对应回复、过程事件和子会话。"""
        with status_lock:
            ensure_session_idle(session_id)
            session = service.get_session(session_id)
            try:
                service.rewrite_latest_user_turn(session, message_id, None)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="会话不存在") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            failures.pop(session_id, None)
            return with_status(service.describe_session(session))

    @router.get("/v1/sessions/{session_id}/events/stream")
    def stream_session_events(session_id: str, after: int = -1) -> StreamingResponse:
        """按会话事件序号推送过程状态，断线后可从 after 继续。"""
        if after < -1:
            raise HTTPException(status_code=400, detail="after 必须大于或等于 -1")
        if not service.get_session(session_id)._path.exists():
            raise HTTPException(status_code=404, detail="会话不存在")

        def events():
            """从已保存事件续发状态，任务结束且事件排空后关闭。"""
            cursor = after
            live_revision = -1
            while True:
                with status_lock:
                    session = service.get_session(session_id)
                    saved = list(session.events)
                    child = sub_agent_snapshot(session_id, workspace.sessions_dir)
                    running = session_id in runs or child is not None or has_active_sub_agents(session_id, workspace.sessions_dir)
                for index in range(cursor + 1, len(saved)):
                    cursor = index
                    event = saved[index]
                    is_step = event.get("kind") == "status" and event.get("title") in ("执行中", "步骤完成", "步骤失败", "执行失败")
                    if is_step or event.get("kind") in ("tool_status", "subagent", "message_saved", "context_usage", "compression"):
                        visible = {key: event[key] for key in ("kind", "title", "content", "created_at", "meta") if key in event}
                        yield f'id: {index}\nevent: progress\ndata: {json.dumps(visible, ensure_ascii=False)}\n\n'
                if child is not None and child["revision"] != live_revision:
                    live_revision = child["revision"]
                    yield f'event: live_text\ndata: {json.dumps({"text": child["text"]}, ensure_ascii=False)}\n\n'
                if not running:
                    break
                time.sleep(0.2)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.delete("/v1/sessions/{session_id}")
    def delete_session_api(session_id: str) -> dict[str, Any]:
        with status_lock:
            ensure_session_idle(session_id)
            deleted = service.delete_session(session_id)
            failures.pop(session_id, None)
        if not deleted:
            raise HTTPException(status_code=404, detail="session not found")
        return {"deleted": True}

    @router.post("/v1/chat/completions")
    def chat_completions(request: ChatCompletionRequest):
        """执行一次 Agent 对话；流式请求持续发送标准聊天片段。"""
        user_input = _latest_user_text(request.messages)
        if not user_input:
            raise HTTPException(status_code=400, detail="messages 中缺少 user 内容")
        if request.edit_message_id is not None and not request.session_id:
            raise HTTPException(status_code=400, detail="编辑消息需要指定会话 ID")

        with status_lock:
            if request.session_id in runs:
                raise HTTPException(status_code=409, detail="会话已有任务正在执行，请等待完成")
            if request.edit_message_id is not None:
                ensure_session_idle(request.session_id)
            session = service.get_session(request.session_id) if request.session_id else service.create_session()
            if request.edit_message_id is not None:
                if not session._path.exists():
                    raise HTTPException(status_code=404, detail="会话不存在")
                latest_user = next((message for message in reversed(session.messages)
                                    if message.get("role") == "user"), None)
                if latest_user is None or latest_user.get("uuid") != request.edit_message_id:
                    raise HTTPException(status_code=409, detail="只能编辑最后一条用户消息，请刷新会话")
            runs[session.session_id] = "queued"
            failures.pop(session.session_id, None)
        console = Console(file=StringIO(), highlight=False)

        def execute(on_delta=None):
            """统一维护非流式和流式运行状态。"""
            try:
                # MCP 工具注册表和后台 Agent 状态是进程级的。
                with _agent_run_lock:
                    with status_lock:
                        runs[session.session_id] = "running"
                    if request.edit_message_id is not None:
                        service.rewrite_latest_user_turn(session, request.edit_message_id, user_input)
                    kwargs = dict(
                        user_input=user_input,
                        session=session,
                        console=console,
                        workspace=workspace,
                        auto_confirm=True,
                        plan_enabled=request.plan_enabled,
                    )
                    if request.edit_message_id is not None:
                        kwargs["record_user_message"] = False
                    if on_delta is not None:
                        kwargs["on_delta"] = on_delta
                    return run_agent(**kwargs)
            except Exception as exc:
                with status_lock:
                    failures[session.session_id] = str(exc)
                raise
            finally:
                with status_lock:
                    runs.pop(session.session_id, None)

        if request.stream:
            chunks: Queue[tuple[str, str]] = Queue(maxsize=256)
            connected = Event()
            connected.set()
            created = int(time.time())
            completion_id = f"chatcmpl-{uuid.uuid4().hex}"

            def enqueue(kind: str, value: str) -> None:
                """有界排队；页面离开时停止送片段，Agent 继续执行。"""
                while connected.is_set():
                    try:
                        chunks.put((kind, value), timeout=0.2)
                        return
                    except Full:
                        continue

            def publish(delta: str) -> None:
                enqueue("delta", delta)

            def worker() -> None:
                """客户端离开后仍让 Agent 完成并保存会话。"""
                try:
                    execute(publish)
                    enqueue("done", "")
                except Exception as exc:
                    enqueue("error", str(exc))

            Thread(target=worker, name=f"chat-{session.session_id}", daemon=True).start()

            def frame(delta: dict[str, Any], finish_reason=None) -> str:
                """生成外部客户端可解析的聊天完成片段。"""
                payload = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                           "model": request.model or MODEL_ID,
                           "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
                return f'data: {json.dumps(payload, ensure_ascii=False)}\n\n'

            def stream():
                """从后台队列读取正文，结束或断开时停止排队片段。"""
                try:
                    yield frame({"role": "assistant", "content": ""})
                    while True:
                        try:
                            kind, value = chunks.get(timeout=0.5)
                        except Empty:
                            yield ": ping\n\n"
                            continue
                        if kind == "delta":
                            yield frame({"content": value})
                        elif kind == "done":
                            yield frame({}, "stop")
                            yield "data: [DONE]\n\n"
                            break
                        else:
                            yield f'data: {json.dumps({"error": {"message": value}}, ensure_ascii=False)}\n\n'
                            yield "data: [DONE]\n\n"
                            break
                finally:
                    connected.clear()

            return StreamingResponse(stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                              "X-QRClaw-Session-ID": session.session_id})

        try:
            content = execute()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        content = content or ""
        created = int(time.time())
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": created,
            "model": request.model or MODEL_ID,
            "session_id": session.session_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                **session.run_usage,
            },
        }

    return router


def _latest_user_text(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role != "user":
            continue
        content = message.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        chunks.append(str(item.get("text") or ""))
                    elif "text" in item:
                        chunks.append(str(item.get("text") or ""))
            return "\n".join(chunks).strip()
    return ""
