from __future__ import annotations

import time
import uuid
from threading import Lock
from io import StringIO
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from rich.console import Console

from qrclaw.agent import run as run_agent
from qrclaw.workspace import Workspace

from .schemas import (
    ChatCompletionRequest,
    ChatMessage,
    CreateSessionRequest,
    SessionDetail,
    SessionSummary,
)
from .session_service import SessionService

MODEL_ID = "qrclaw-agent"
_agent_run_lock = Lock()


def build_router(workspace: Workspace) -> APIRouter:
    router = APIRouter()
    service = SessionService(workspace)

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
        return service.list_sessions()

    @router.post("/v1/sessions", response_model=SessionDetail)
    def create_session_api(request: CreateSessionRequest | None = Body(default=None)) -> dict[str, Any]:
        session = service.create_session(request.session_id if request else None)
        return service.describe_session(session)

    @router.get("/v1/sessions/{session_id}", response_model=SessionDetail)
    def get_session_api(session_id: str) -> dict[str, Any]:
        session = service.get_session(session_id)
        return service.describe_session(session)

    @router.delete("/v1/sessions/{session_id}")
    def delete_session_api(session_id: str) -> dict[str, Any]:
        deleted = service.delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="session not found")
        return {"deleted": True}

    @router.post("/v1/chat/completions")
    def chat_completions(request: ChatCompletionRequest) -> dict[str, Any]:
        if request.stream:
            raise HTTPException(status_code=400, detail="stream=true 暂未支持，当前仅支持非流式调用")

        user_input = _latest_user_text(request.messages)
        if not user_input:
            raise HTTPException(status_code=400, detail="messages 中缺少 user 内容")

        session = service.get_session(request.session_id) if request.session_id else service.create_session()
        console = Console(file=StringIO(), highlight=False)

        try:
            # MCP 工具注册表和后台 Agent 状态是进程级的。
            with _agent_run_lock:
                content = run_agent(
                    user_input=user_input,
                    session=session,
                    console=console,
                    workspace=workspace,
                    auto_confirm=True,
                )
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
