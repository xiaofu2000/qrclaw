"""OpenAI-compatible local server adapter."""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from qrclaw.workspace import Workspace
from .system_routes import build_system_router
from .workspace_hub import WorkspaceDispatchMiddleware, WorkspaceHub


LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def _hostname(value: str) -> str:
    """从 Host 头或 Origin 取出主机名，去掉端口和 IPv6 方括号；无法解析时返回空串。"""
    try:
        return (urlsplit(value if "://" in value else f"//{value}").hostname or "").lower()
    except ValueError:
        return ""


def create_app(workspace: Workspace | None = None) -> FastAPI:
    workspace = workspace or Workspace(agent_id="default")
    hub = WorkspaceHub(initial_workspace=workspace)

    @asynccontextmanager
    async def lifespan(app):
        """恢复已加载工作空间的持久化记忆批次，退出时统一停止后台线程并关闭常驻 MCP 连接。"""
        from qrclaw.agent import _cleanup_mcp
        hub.startup()
        try:
            yield
        finally:
            hub.shutdown()
            # MCP 连接随服务常驻，服务退出时断开；等待线程结束不阻塞事件循环。
            await asyncio.to_thread(_cleanup_mcp)

    app = FastAPI(title="QRClaw Local API", version="0.1.0", lifespan=lifespan)
    app.state.workspace_hub = hub
    static_dir = Path(__file__).with_name("static")

    # 内层工作空间分发中间件：在通过外层本机鉴权与 CORS 后，将会话/对话/记忆请求路由到目标 Workspace
    app.add_middleware(WorkspaceDispatchMiddleware, hub=hub)

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        """本机访问免令牌，但只接受本机域名与本机页面发起的请求；其他情况需要 QRCLAW_API_TOKEN。"""
        token = os.getenv("QRCLAW_API_TOKEN", "")
        supplied = request.headers.get("authorization", "")
        if token and hmac.compare_digest(supplied, f"Bearer {token}"):
            # 浏览器不会自动携带 Authorization，带对令牌的请求不可能来自恶意网页。
            return await call_next(request)
        client = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(client).is_loopback
        except ValueError:
            local = False
        if not local:
            return JSONResponse({"detail": "远程访问需要 QRCLAW_API_TOKEN"}, status_code=403)
        # 防 DNS 重绑定：恶意域名解析到 127.0.0.1 后，请求来自本机但 Host 仍是该域名。
        if _hostname(request.headers.get("host", "")) not in LOCAL_HOSTNAMES:
            return JSONResponse({"detail": "只接受通过 127.0.0.1 或 localhost 访问"}, status_code=403)
        # 防跨站请求：其他网站的页面可以向本机发无正文的 POST，浏览器会带上它的 Origin。
        origin = request.headers.get("origin")
        if origin is not None and _hostname(origin) not in LOCAL_HOSTNAMES:
            return JSONResponse({"detail": "拒绝来自其他网站的请求"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def bad_input(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    app.include_router(build_system_router(hub))

    return app


app = create_app()
