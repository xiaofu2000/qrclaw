from __future__ import annotations

import time
import uuid
import json
from queue import Empty, Full, Queue
from threading import Event, Thread
from threading import BoundedSemaphore, RLock
from io import StringIO
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from rich.console import Console

from qrclaw.agent import run as run_agent
from qrclaw.cancellation import (
    CancelToken,
    TaskCancelledError,
    bind_cancel_token,
    cancel_session_run,
    register_session_token,
    unregister_session_token,
)
from qrclaw.config import MAX_CONCURRENT_RUNS
from qrclaw.memory.context.session.events import STREAMED_STATUS_CODES, SessionEvent, status_code, status_event
from qrclaw.subagent_progress import cancel_sub_agents_for_parent, has_active_sub_agents, sub_agent_snapshot
from qrclaw.tools.spawn_agent import cancel_tasks_for_session
from qrclaw.workspace import Workspace
from qrclaw.logger import get_logger

from .schemas import (
    ChatCompletionRequest,
    ChatMessage,
    CreateSessionRequest,
    RenameSessionRequest,
    SessionDetail,
    SessionSummary,
)
from .session_service import SessionService
from .session_titles import schedule_title

MODEL_ID = "qrclaw-agent"
logger = get_logger("qrclaw.server.routes")
# 同时执行的对话数上限，超出的请求显示「等待执行」。每次运行的会话、工作空间、上下文和记忆节点都按线程隔离，
# 同一会话不会并发（见 chat_completions 里的 runs 检查），上限只用来避免同时向模型发出过多请求。
_run_slots = BoundedSemaphore(MAX_CONCURRENT_RUNS)


def build_router(workspace: Workspace) -> APIRouter:
    router = APIRouter()
    service = SessionService(workspace)
    from .wiki_routes import build_wiki_router
    router.include_router(build_wiki_router(workspace))
    runs: dict[str, str] = {}
    failures: dict[str, str] = {}
    run_done_events: dict[str, Event] = {}
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
    def get_session_api(session_id: str, messages_from: int = 0, events_from: int = 0,
                        history_version: int | None = None) -> dict[str, Any]:
        """读取会话；带上已有的历史版本和长度时只返回新增部分，避免运行中反复下载整份会话。"""
        # 避免返回旧内容和刚完成的 idle 状态，让页面误以为结果已完整。
        with status_lock:
            session = service.get_session(session_id)
            if not session._path.exists():
                raise HTTPException(status_code=404, detail="会话不存在")
            return with_status(service.describe_session(session, messages_from=messages_from, events_from=events_from,
                                                        history_version=history_version))

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
                    is_step = status_code(event) in STREAMED_STATUS_CODES
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

    @router.post("/v1/sessions/{session_id}/cancel")
    def cancel_session_api(session_id: str) -> dict[str, Any]:
        """停止指定会话正在排队或运行的任务及关联子任务。"""
        with status_lock:
            session = service.get_session(session_id)
            if not session._path.exists():
                raise HTTPException(status_code=404, detail="会话不存在")
            current_status = runs.get(session_id)
            done_event = run_done_events.get(session_id)
            running_child = sub_agent_snapshot(session_id, workspace.sessions_dir) is not None
            active_subs = has_active_sub_agents(session_id, workspace.sessions_dir)
            is_active = current_status in ("queued", "running") or running_child or active_subs

        if not is_active:
            return {"cancelled": False, "session_id": session_id, "run_status": "idle"}

        cancel_session_run(workspace.sessions_dir, session_id)
        cancel_tasks_for_session(workspace.sessions_dir, session_id, join_timeout=0.5)
        if done_event is not None:
            done_event.wait(timeout=2.0)
        cancel_sub_agents_for_parent(workspace.sessions_dir, session_id)

        with status_lock:
            still_child = sub_agent_snapshot(session_id, workspace.sessions_dir) is not None
            final_status = runs.get(session_id, "running" if still_child else "idle")
            failures.pop(session_id, None)
        return {"cancelled": True, "session_id": session_id, "run_status": final_status}

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
            from qrclaw.agent import _runner
            memory_integration = _runner.react_loop._get_memory_integration(workspace, session)
            # 请求排队期间也有新活动，不能把等待执行误判成会话闲置。
            memory_integration.on_run_start()
            initial_user_uuid = next(
                (m.get("uuid") for m in reversed(session.messages) if m.get("role") == "user"),
                None,
            )
            token: CancelToken = register_session_token(workspace.sessions_dir, session.session_id)
            done_event = Event()
            run_done_events[session.session_id] = done_event
            runs[session.session_id] = "queued"
            failures.pop(session.session_id, None)
        console = Console(file=StringIO(), highlight=False)

        def _finalize_cancelled_session(rewritten: bool, streamed_text: str) -> str:
            """取消任务时补齐未入库的当轮用户消息、部分流式正文，并写入 run_cancelled 事件。"""
            session._load(strict=False)
            if not session._path.exists():
                return ""
            if request.edit_message_id is not None:
                if not rewritten:
                    try:
                        service.rewrite_latest_user_turn(session, request.edit_message_id, user_input)
                    except Exception as exc:
                        logger.warning("取消编辑请求时重写末轮消息失败：%s", exc)
            else:
                latest_uuid = next(
                    (m.get("uuid") for m in reversed(session.messages) if m.get("role") == "user"),
                    None,
                )
                if latest_uuid == initial_user_uuid:
                    session.add({"role": "user", "content": user_input})

            session._load(strict=False)
            partial_saved = ""
            if streamed_text:
                user_idx = next(
                    (i for i in range(len(session.messages) - 1, -1, -1) if session.messages[i].get("role") == "user"),
                    -1,
                )
                turn_assistants = [
                    m.get("content", "")
                    for m in session.messages[user_idx + 1:]
                    if m.get("role") == "assistant"
                    and m.get("kind") != "execution_report"
                    and isinstance(m.get("content"), str)
                ] if user_idx >= 0 else []
                already_saved = "".join(turn_assistants)
                unsaved = (
                    streamed_text[len(already_saved):]
                    if streamed_text.startswith(already_saved)
                    else (streamed_text if not already_saved else "")
                )
                if unsaved.strip():
                    session.add({"role": "assistant", "content": unsaved})
                    session.add_event(SessionEvent(kind="message_saved", title="消息已保存"))
                    partial_saved = unsaved

            session.add_event(status_event("run_cancelled", "任务已由用户停止"))
            return partial_saved

        def execute(on_delta=None):
            """统一维护非流式和流式运行状态，支持排队与执行期随时取消。"""
            streamed_chunks: list[str] = []
            rewritten = False
            slot_acquired = False

            def tracked_delta(delta: str) -> None:
                streamed_chunks.append(delta)
                if on_delta is not None:
                    on_delta(delta)

            try:
                with bind_cancel_token(token):
                    while not _run_slots.acquire(timeout=0.05):
                        token.check()
                    slot_acquired = True
                    try:
                        token.check()
                        with status_lock:
                            runs[session.session_id] = "running"
                        if request.edit_message_id is not None:
                            service.rewrite_latest_user_turn(session, request.edit_message_id, user_input)
                            rewritten = True
                        token.check()
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
                            kwargs["on_delta"] = tracked_delta
                        result = run_agent(**kwargs)
                        token.check()
                    finally:
                        if slot_acquired:
                            _run_slots.release()
                if not session.title:
                    schedule_title(workspace.sessions_dir, session.session_id)
                return result
            except TaskCancelledError:
                _finalize_cancelled_session(rewritten, "".join(streamed_chunks))
                with status_lock:
                    failures.pop(session.session_id, None)
                raise
            except Exception as exc:
                if token.is_cancelled():
                    _finalize_cancelled_session(rewritten, "".join(streamed_chunks))
                    with status_lock:
                        failures.pop(session.session_id, None)
                    raise TaskCancelledError() from exc
                with status_lock:
                    failures[session.session_id] = str(exc)
                raise
            finally:
                if token.is_cancelled():
                    cancel_tasks_for_session(workspace.sessions_dir, session.session_id, join_timeout=0.3)
                    cancel_sub_agents_for_parent(workspace.sessions_dir, session.session_id)
                if memory_integration._busy:
                    try:
                        memory_integration.on_react_loop_end(session)
                    except Exception as exc:
                        # Agent 初始化或替代运行器异常时也解除闲置暂停。
                        logger.warning("会话记忆提取收尾失败：%s", exc, exc_info=True)
                unregister_session_token(workspace.sessions_dir, session.session_id, token)
                with status_lock:
                    runs.pop(session.session_id, None)
                    if run_done_events.get(session.session_id) is done_event:
                        run_done_events.pop(session.session_id, None)
                    done_event.set()

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
                """客户端离开后仍让 Agent 完成并保存会话；用户主动停止时发送正常结束帧。"""
                try:
                    execute(publish)
                    enqueue("done", "")
                except TaskCancelledError:
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
        except TaskCancelledError:
            content = ""
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
