"""OpenAI-compatible local server adapter."""
from __future__ import annotations

import time
import uuid
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from rich.console import Console

from qrclaw.agent import run as run_agent
from qrclaw.memory.context.session import Session
from qrclaw.workspace import Workspace

MODEL_ID = "qrclaw-agent"


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"] | str
    content: str | list[Any] | None = ""


class ChatCompletionRequest(BaseModel):
    model: str = MODEL_ID
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "qrclaw"


def create_app() -> FastAPI:
    app = FastAPI(title="QRClaw Local API", version="0.1.0")
    static_dir = Path(__file__).with_name("static")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8765", "http://localhost:8765"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "qrclaw-local-api"}

    @app.get("/v1/models")
    def models() -> dict:
        return {
            "object": "list",
            "data": [ModelCard(id=MODEL_ID, created=int(time.time())).model_dump()],
        }

    @app.post("/v1/chat/completions")
    def chat_completions(request: ChatCompletionRequest) -> dict:
        if request.stream:
            raise HTTPException(status_code=400, detail="stream=true 暂未支持，当前仅支持非流式调用")

        user_input = _latest_user_text(request.messages)
        if not user_input:
            raise HTTPException(status_code=400, detail="messages 中缺少 user 内容")

        workspace = Workspace(agent_id="default")
        session = Session(sessions_dir=workspace.sessions_dir, resume=True)
        console = Console(file=StringIO(), highlight=False)

        try:
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
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": session.prompt_tokens,
                "completion_tokens": session.completion_tokens,
                "total_tokens": session.total_tokens,
            },
        }

    return app


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


app = create_app()
