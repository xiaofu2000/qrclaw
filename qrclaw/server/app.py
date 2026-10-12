"""OpenAI-compatible local server adapter."""
from __future__ import annotations

import hmac
import ipaddress
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from qrclaw.workspace import Workspace
from .routes import build_router


def create_app(workspace: Workspace | None = None) -> FastAPI:
    workspace = workspace or Workspace(agent_id="default")

    @asynccontextmanager
    async def lifespan(app):
        """恢复持久化的记忆批次，退出时取消尚未到期的闲置计时。"""
        from qrclaw.agent import _runner
        _runner.react_loop.resume_memory_extractions(workspace)
        try:
            yield
        finally:
            _runner.react_loop.stop_memory_extractions(workspace)

    app = FastAPI(title="QRClaw Local API", version="0.1.0", lifespan=lifespan)
    static_dir = Path(__file__).with_name("static")

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        host = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            token = os.getenv("QRCLAW_API_TOKEN", "")
            supplied = request.headers.get("authorization", "")
            if not token or not hmac.compare_digest(supplied, f"Bearer {token}"):
                return JSONResponse({"detail": "远程访问需要 QRCLAW_API_TOKEN"}, status_code=403)
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

    app.include_router(build_router(workspace))

    return app


app = create_app()
