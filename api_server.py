"""
最小 OpenWebUI API 对接服务

启动: python api_server.py
OpenWebUI 配置: http://localhost:8000/v1 (API URL), key 随便填
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal
import uvicorn

from qrclaw.agent import run
from qrclaw.workspace import Workspace
from qrclaw.memory.context.session import Session
from qrclaw.memory.context.context_manager import init_context_manager
from rich.console import Console


# ── Pydantic 模型 ────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "qrclaw"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None


class ChatResponseChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatResponseChoice]


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "qrclaw"


class ModelsResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


# ── 全局状态 ─────────────────────────────────────────────────────────────────

_workspace: Workspace | None = None
_sessions_dir: Path | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 workspace"""
    global _workspace, _sessions_dir

    # 使用默认 agent workspace
    _workspace = Workspace(agent_id="api-server")
    _sessions_dir = _workspace.sessions_dir

    print(f"✅ QrClaw API Server 启动")
    print(f"   Workspace: {_workspace.root}")
    print(f"   Sessions:  {_sessions_dir}")
    print(f"   OpenWebUI 配置: http://localhost:8000/v1")

    yield

    print("\n👋 服务关闭")


app = FastAPI(title="QrClaw API", lifespan=lifespan)


# ── API 端点 ─────────────────────────────────────────────────────────────────

@app.get("/v1/models")
async def list_models() -> ModelsResponse:
    """OpenWebUI 会调用这个获取可用模型列表"""
    return ModelsResponse(
        data=[ModelInfo(id="qrclaw")]
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    """
    核心对话接口

    注意：这是最小版本，暂不支持：
    - 流式输出 (stream=true 会忽略)
    - 多轮对话保持（每次新建 session）
    - 温度控制
    """
    # 提取最后一条用户消息
    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        return _make_error_response("No user message found")

    user_input = user_messages[-1].content

    # 创建新 session（最小版本，每次新建）
    session_id = f"api-{uuid.uuid4().hex[:8]}"
    session = Session(
        sessions_dir=_sessions_dir,
        session_id=session_id,
        resume=False,
    )

    # 添加历史消息到 session（让 agent 能看到上下文）
    for msg in request.messages[:-1]:  # 除了最后一条用户消息
        session.add({"role": msg.role, "content": msg.content})

    # 执行 agent
    try:
        # 创建一个简单的 console（不输出到终端）
        from io import StringIO
        console = Console(file=StringIO(), force_terminal=False)

        result = run(
            user_input=user_input,
            session=session,
            console=console,
            workspace=_workspace,
            auto_confirm=False,  # API 模式可以改为 True 自动确认
        )
    except Exception as e:
        import traceback
        result = f"执行出错: {e}\n{traceback.format_exc()}"

    # 返回 OpenAI 格式
    response_id = f"chatcmpl-{uuid.uuid4().hex}"
    return ChatResponse(
        id=response_id,
        created=int(__import__('time').time()),
        model=request.model or "qrclaw",
        choices=[
            ChatResponseChoice(
                message=ChatMessage(role="assistant", content=result or "（无返回结果）")
            )
        ],
    )


def _make_error_response(message: str) -> ChatResponse:
    return ChatResponse(
        id=f"chatcmpl-error",
        created=int(__import__('time').time()),
        model="qrclaw",
        choices=[
            ChatResponseChoice(
                message=ChatMessage(role="assistant", content=f"Error: {message}")
            )
        ],
    )


# ── 健康检查 ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "workspace": str(_workspace.root) if _workspace else None}


# ── 启动入口 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
