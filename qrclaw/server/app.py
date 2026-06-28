"""OpenAI-compatible local server adapter."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from qrclaw.workspace import Workspace
from .routes import build_router


def create_app() -> FastAPI:
    app = FastAPI(title="QRClaw Local API", version="0.1.0")
    static_dir = Path(__file__).with_name("static")

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

    workspace = Workspace(agent_id="default")
    app.include_router(build_router(workspace))

    return app


app = create_app()
