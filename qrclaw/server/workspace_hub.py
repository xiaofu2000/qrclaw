"""多工作空间（Agent）运行时路由与生命周期中心。

在不改动 ``routes.py`` 与 ``wiki_routes.py`` 的前提下，按 ``agent_id`` 懒加载并缓存各自独立的
``Workspace`` 及子 ``FastAPI`` 实例，实现跨项目的会话、运行状态与 Wiki 记忆完全隔离。
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

import qrclaw.workspace as workspace_module
from qrclaw.logger import get_logger
from qrclaw.workspace import Workspace, is_valid_agent_id

from .routes import build_router
from .session_service import SessionService

logger = get_logger("qrclaw.server.workspace_hub")

_WORKSPACE_EXACT_PATHS = frozenset({"/health", "/v1/models", "/v1/sessions", "/v1/chat", "/v1/wiki"})
_WORKSPACE_PREFIXES = ("/v1/sessions/", "/v1/chat/", "/v1/wiki/")


def _is_workspace_route(path: str) -> bool:
    """判断路径是否应转发给具体工作空间的子应用处理。"""
    return path in _WORKSPACE_EXACT_PATHS or path.startswith(_WORKSPACE_PREFIXES)


@dataclass
class _WorkspaceRuntime:
    workspace: Workspace
    router: APIRouter
    sub_app: FastAPI
    list_sessions_endpoint: Callable[[], list[dict[str, Any]]] | None = None
    recovered: bool = False


class WorkspaceHub:
    """管理默认激活工作空间与各工作空间子应用的按需懒加载、状态检测和优雅关闭。"""

    def __init__(self, initial_workspace: Workspace | None = None):
        self._lock = RLock()
        initial = initial_workspace or Workspace(agent_id="default")
        self._active_agent_id: str = initial.agent_id
        self._started: bool = False
        self._runtimes: dict[str, _WorkspaceRuntime] = {}
        self._create_runtime(initial)

    @property
    def active_agent_id(self) -> str:
        with self._lock:
            return self._active_agent_id

    @property
    def active_workspace(self) -> Workspace:
        return self.get_runtime(self.active_agent_id).workspace

    def has_workspace(self, agent_id: str) -> bool:
        """检查工作空间是否已存在（避免非法请求头在磁盘上静默创建幽灵目录）。"""
        if not is_valid_agent_id(agent_id):
            return False
        with self._lock:
            if agent_id in self._runtimes or agent_id == "default":
                return True
        return (workspace_module.AGENTS_ROOT / agent_id).is_dir()

    def _create_runtime(self, ws: Workspace) -> _WorkspaceRuntime:
        sub_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @sub_app.exception_handler(ValueError)
        async def _bad_input(request: Request, exc: ValueError):
            return JSONResponse({"detail": str(exc)}, status_code=400)

        router = build_router(ws)
        sub_app.include_router(router)

        list_endpoint = None
        for route in router.routes:
            if getattr(route, "path", None) == "/v1/sessions" and "GET" in (getattr(route, "methods", None) or set()):
                list_endpoint = route.endpoint
                break

        runtime = _WorkspaceRuntime(
            workspace=ws,
            router=router,
            sub_app=sub_app,
            list_sessions_endpoint=list_endpoint,
            recovered=False,
        )
        self._runtimes[ws.agent_id] = runtime
        return runtime

    def _recover_runtime(self, runtime: _WorkspaceRuntime) -> None:
        """为工作空间恢复持久化的会话变更、待提取记忆批次与 Wiki 修复队列。"""
        if runtime.recovered:
            return
        from qrclaw.agent import _runner
        from qrclaw.memory.wiki import WikiMemory

        ws = runtime.workspace
        SessionService(ws).recover_session_changes()
        _runner.react_loop.resume_memory_extractions(ws)
        wiki = WikiMemory.for_workspace(ws.memory_dir)
        wiki.rebuild_index()
        wiki.dream_worker.recover_interrupted()
        wiki.repair_worker.closed = False
        wiki.repair_worker.resume()
        runtime.recovered = True
        logger.info("工作空间运行时已就绪: %s (%s)", ws.agent_id, ws.root)

    def _stop_runtime(self, runtime: _WorkspaceRuntime, *, clear_wiki_cache: bool = False) -> None:
        """停止工作空间的后台记忆提取、Wiki 修复与记忆做梦线程。"""
        from qrclaw.agent import _runner
        from qrclaw.memory import wiki as wiki_pkg
        from qrclaw.memory.wiki import WikiMemory
        import qrclaw.memory.wiki.wiki_memory as wiki_memory_mod

        ws = runtime.workspace
        if runtime.recovered:
            try:
                _runner.react_loop.stop_memory_extractions(ws)
            except Exception as exc:
                logger.warning("停止记忆提取失败 (%s): %s", ws.agent_id, exc)
            try:
                wiki = WikiMemory.for_workspace(ws.memory_dir)
                wiki.repair_worker.close()
                wiki.dream_worker.close()
            except Exception as exc:
                logger.warning("关闭 Wiki 后台线程失败 (%s): %s", ws.agent_id, exc)
            runtime.recovered = False

        if clear_wiki_cache:
            resolved = ws.memory_dir.resolve()
            with wiki_memory_mod._instances_lock:
                cached = wiki_memory_mod._instances.pop(resolved, None)
            if cached is not None:
                try:
                    cached.repair_worker.close()
                except Exception:
                    pass
                try:
                    cached.dream_worker.close()
                except Exception:
                    pass
            _ = wiki_pkg

    def startup(self) -> None:
        """主应用 lifespan 启动时恢复所有已注册工作空间。"""
        with self._lock:
            self._started = True
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            with self._lock:
                self._recover_runtime(runtime)

    def shutdown(self) -> None:
        """主应用 lifespan 退出时优雅关闭所有已加载工作空间。"""
        with self._lock:
            self._started = False
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            self._stop_runtime(runtime)

    def get_runtime(self, agent_id: str | None = None) -> _WorkspaceRuntime:
        """获取或懒加载指定工作空间的运行时。"""
        with self._lock:
            target = agent_id or self._active_agent_id
            if not is_valid_agent_id(target):
                raise ValueError(f"无效的工作空间 ID: {target!r}")
            runtime = self._runtimes.get(target)
            if runtime is None:
                if not self.has_workspace(target):
                    raise LookupError(f"工作空间 '{target}' 不存在")
                ws = Workspace(agent_id=target)
                runtime = self._create_runtime(ws)
            if self._started and not runtime.recovered:
                self._recover_runtime(runtime)
            return runtime

    def resolve_workspace(self, request: Request) -> Workspace:
        """从请求头 X-QRClaw-Workspace 或当前激活工作空间解析目标 Workspace。"""
        header_val = request.headers.get("x-qrclaw-workspace", "").strip()
        if header_val:
            if not is_valid_agent_id(header_val):
                raise HTTPException(status_code=400, detail=f"无效的工作空间 ID: {header_val}")
            if not self.has_workspace(header_val):
                raise HTTPException(status_code=404, detail=f"工作空间 '{header_val}' 不存在")
            return self.get_runtime(header_val).workspace
        return self.active_workspace

    def switch(self, agent_id: str) -> Workspace:
        """切换默认激活的工作空间，按需初始化对应子应用。"""
        cleaned = (agent_id or "").strip()
        if not is_valid_agent_id(cleaned):
            raise ValueError(f"无效的工作空间 ID: {agent_id!r}")
        if not self.has_workspace(cleaned):
            raise LookupError(f"工作空间 '{cleaned}' 不存在")
        with self._lock:
            runtime = self.get_runtime(cleaned)
            self._active_agent_id = cleaned
            logger.info("已切换当前激活工作空间: %s", cleaned)
            return runtime.workspace

    def has_active_runs(self, agent_id: str) -> bool:
        """检查指定工作空间是否仍有排队中、运行中、后台子 Agent 或记忆做梦任务。"""
        with self._lock:
            runtime = self._runtimes.get(agent_id)
        if runtime is None:
            return False
        try:
            from qrclaw.memory.wiki import WikiMemory
            if WikiMemory.for_workspace(runtime.workspace.memory_dir).dream_worker.active_count() > 0:
                return True
        except Exception as exc:
            logger.warning("检查工作空间做梦状态失败 (%s): %s", agent_id, exc)
        if runtime.list_sessions_endpoint is None:
            return False
        try:
            summaries = runtime.list_sessions_endpoint()
            return any(
                item.get("run_status") in ("running", "queued") or bool(item.get("active_subagents"))
                for item in summaries
            )
        except Exception as exc:
            logger.warning("检查工作空间运行状态失败 (%s): %s", agent_id, exc)
            return False

    def remove_workspace(self, agent_id: str) -> None:
        """从 Hub 卸载指定工作空间并停止其后台线程（删除工作空间前调用）。"""
        with self._lock:
            runtime = self._runtimes.pop(agent_id, None)
        if runtime is not None:
            self._stop_runtime(runtime, clear_wiki_cache=True)

    def get_known_workspace(self, agent_id: str) -> Workspace | None:
        """若工作空间已在内存中则返回该实例，避免覆盖自定义 _root（如测试临时目录）。"""
        with self._lock:
            runtime = self._runtimes.get(agent_id)
            return runtime.workspace if runtime is not None else None


class WorkspaceDispatchMiddleware:
    """将工作空间级 API 请求动态路由到目标 Workspace 的专属子应用。"""

    def __init__(self, app: Any, hub: WorkspaceHub):
        self.app = app
        self.hub = hub

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not _is_workspace_route(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        requested_id = ""
        for raw_key, raw_val in scope.get("headers") or ():
            if raw_key == b"x-qrclaw-workspace":
                requested_id = raw_val.decode("latin-1", errors="ignore").strip()
                break

        if requested_id:
            if not is_valid_agent_id(requested_id):
                resp = JSONResponse({"detail": f"无效的工作空间 ID: {requested_id}"}, status_code=400)
                await resp(scope, receive, send)
                return
            if not self.hub.has_workspace(requested_id):
                resp = JSONResponse({"detail": f"工作空间 '{requested_id}' 不存在"}, status_code=404)
                await resp(scope, receive, send)
                return
            target_id = requested_id
        else:
            target_id = self.hub.active_agent_id

        try:
            runtime = self.hub.get_runtime(target_id)
        except LookupError as exc:
            resp = JSONResponse({"detail": str(exc)}, status_code=404)
            await resp(scope, receive, send)
            return
        except ValueError as exc:
            resp = JSONResponse({"detail": str(exc)}, status_code=400)
            await resp(scope, receive, send)
            return

        await runtime.sub_app(scope, receive, send)
