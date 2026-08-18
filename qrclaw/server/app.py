"""QRClaw 本地工作台 HTTP 与 WebSocket 服务。"""

from __future__ import annotations

import asyncio
import hmac
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from qrclaw import config_manager
from qrclaw.execution.service import RunService
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.server.app")
API_PREFIX = "/api/v1"


class ApiError(RuntimeError):
    """可稳定映射为协议错误响应的异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


class CreateConversationRequest(BaseModel):
    """创建会话请求。"""

    title: str = Field(min_length=1, max_length=200)
    workspace_path: str = Field(min_length=1)


class UpdateConversationRequest(BaseModel):
    """修改会话请求。"""

    title: str = Field(min_length=1, max_length=200)


class CreateRunRequest(BaseModel):
    """创建任务运行请求。"""

    conversation_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    client_request_id: str = Field(min_length=1, max_length=200)


class CancelRunRequest(BaseModel):
    """取消任务请求。"""

    reason: str = "user_requested"


class ResolveApprovalRequest(BaseModel):
    """处理工具授权请求。"""

    decision: Literal["allow_once", "deny"]


class SettingsRequest(BaseModel):
    """工作台允许修改的本地设置。"""

    provider: str = "litellm"
    model: str = Field(min_length=1)
    base_url: str = ""
    api_key: str | None = None
    default_workspace: str = ""
    log_level: str = "INFO"


def _request_id(request: Request | None = None) -> str:
    """取得或生成请求追踪 ID。"""

    if request:
        existing = getattr(request.state, "request_id", None)
        if existing:
            return str(existing)
    return f"req_{uuid.uuid4().hex}"


def _success(data: Any, request: Request | None = None, status_code: int = 200) -> JSONResponse:
    """构造统一成功响应。"""

    return JSONResponse(
        status_code=status_code,
        content={"data": data, "request_id": _request_id(request)},
    )


def _mask_secret(secret: str) -> str:
    """生成不会泄露完整 API Key 的掩码。"""

    if not secret:
        return ""
    suffix = secret[-4:] if len(secret) >= 4 else ""
    return f"••••••••{suffix}"


def create_app(service: RunService | None = None) -> FastAPI:
    """创建可注入运行服务的 FastAPI 应用。"""

    runtime = service or RunService()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info("QRClaw 本地工作台服务已启动")
        yield
        logger.info("QRClaw 本地工作台服务已停止")

    application = FastAPI(
        title="QRClaw Local Workbench API",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.run_service = runtime
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8765", "http://localhost:8765"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID", "X-QRClaw-Token"],
    )

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or _request_id()
        if request.url.path != f"{API_PREFIX}/health" and request.method != "OPTIONS":
            supplied = request.headers.get("X-QRClaw-Token", "")
            if not hmac.compare_digest(supplied, runtime.access_token):
                response = JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": "invalid_access_token",
                            "message": "本地访问令牌无效",
                            "retryable": False,
                            "details": {},
                        },
                        "request_id": request.state.request_id,
                    },
                )
                response.headers["X-Request-ID"] = request.state.request_id
                return response
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @application.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "details": exc.details,
                },
                "request_id": _request_id(request),
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "请求参数校验失败",
                    "retryable": False,
                    "details": {"errors": exc.errors()},
                },
                "request_id": _request_id(request),
            },
        )

    @application.get(f"{API_PREFIX}/health")
    async def health(request: Request):
        return _success(
            {"status": "ok", "service": "qrclaw-local-workbench"},
            request,
        )

    @application.get(f"{API_PREFIX}/capabilities")
    async def capabilities(request: Request):
        return _success(
            {
                "protocol_version": "1",
                "features": [
                    "conversations",
                    "run_snapshots",
                    "run_events",
                    "tool_approvals",
                    "run_cancellation",
                    "websocket_replay",
                    "file_changes",
                    "settings",
                ],
                "limits": {"approval_decisions": ["allow_once", "deny"]},
            },
            request,
        )

    @application.get(f"{API_PREFIX}/conversations")
    async def list_conversations(request: Request):
        return _success(runtime.list_conversations(), request)

    @application.post(f"{API_PREFIX}/conversations", status_code=201)
    async def create_conversation(payload: CreateConversationRequest, request: Request):
        try:
            conversation = runtime.create_conversation(payload.title, payload.workspace_path)
        except ValueError as exc:
            raise ApiError("invalid_workspace_path", str(exc), 422) from exc
        return _success(conversation, request, status_code=201)

    @application.get(f"{API_PREFIX}/conversations/{{conversation_id}}")
    async def get_conversation(conversation_id: str, request: Request):
        try:
            conversation = runtime.get_conversation(conversation_id)
        except KeyError as exc:
            raise ApiError("conversation_not_found", "找不到指定会话", 404) from exc
        return _success(conversation, request)

    @application.patch(f"{API_PREFIX}/conversations/{{conversation_id}}")
    async def update_conversation(
        conversation_id: str,
        payload: UpdateConversationRequest,
        request: Request,
    ):
        try:
            conversation = runtime.update_conversation(conversation_id, payload.title)
        except KeyError as exc:
            raise ApiError("conversation_not_found", "找不到指定会话", 404) from exc
        return _success(conversation, request)

    @application.delete(f"{API_PREFIX}/conversations/{{conversation_id}}", status_code=204)
    async def delete_conversation(conversation_id: str):
        try:
            runtime.delete_conversation(conversation_id)
        except KeyError as exc:
            raise ApiError("conversation_not_found", "找不到指定会话", 404) from exc
        except RuntimeError as exc:
            raise ApiError("conversation_has_active_run", str(exc), 409) from exc
        return Response(status_code=204)

    @application.get(f"{API_PREFIX}/conversations/{{conversation_id}}/messages")
    async def get_messages(conversation_id: str, request: Request):
        try:
            messages = runtime.get_messages(conversation_id)
        except KeyError as exc:
            raise ApiError("conversation_not_found", "找不到指定会话", 404) from exc
        return _success(messages, request)

    @application.post(f"{API_PREFIX}/runs", status_code=202)
    async def create_run(payload: CreateRunRequest, request: Request):
        try:
            snapshot = runtime.start_run(
                payload.conversation_id,
                payload.content,
                payload.client_request_id,
            )
        except KeyError as exc:
            raise ApiError("conversation_not_found", "找不到指定会话", 404) from exc
        return _success(
            {"run_id": snapshot.run.run_id, "status": snapshot.run.status.value},
            request,
            status_code=202,
        )

    @application.get(f"{API_PREFIX}/runs/{{run_id}}")
    async def get_run(run_id: str, request: Request):
        try:
            snapshot = runtime.get_snapshot(run_id)
        except KeyError as exc:
            raise ApiError("run_not_found", "找不到指定任务", 404) from exc
        return _success(snapshot.model_dump(mode="json"), request)

    @application.get(f"{API_PREFIX}/runs/{{run_id}}/events")
    async def get_run_events(run_id: str, request: Request, after_seq: int = 0):
        try:
            events = runtime.get_events(run_id, after_seq)
        except KeyError as exc:
            raise ApiError("run_not_found", "找不到指定任务", 404) from exc
        return _success([event.model_dump(mode="json") for event in events], request)

    @application.post(f"{API_PREFIX}/runs/{{run_id}}/cancel", status_code=202)
    async def cancel_run(run_id: str, _: CancelRunRequest, request: Request):
        try:
            snapshot = runtime.cancel_run(run_id)
        except KeyError as exc:
            raise ApiError("run_not_found", "找不到指定任务", 404) from exc
        return _success(
            {"run_id": run_id, "status": snapshot.run.status.value},
            request,
            status_code=202,
        )

    @application.post(f"{API_PREFIX}/tool-approvals/{{approval_id}}/resolve")
    async def resolve_approval(
        approval_id: str,
        payload: ResolveApprovalRequest,
        request: Request,
    ):
        try:
            result = runtime.resolve_approval(approval_id, payload.decision)
        except KeyError as exc:
            raise ApiError("approval_not_found", "找不到待处理授权", 404) from exc
        return _success(result, request)

    @application.get(f"{API_PREFIX}/settings")
    async def get_settings(request: Request):
        from qrclaw.sandbox import is_sandbox_enabled

        config = config_manager.get_config()
        llm = config.get("llm", {})
        api_key = str(llm.get("api_key", ""))
        return _success(
            {
                "provider": llm.get("provider", "litellm"),
                "model": llm.get("model", ""),
                "base_url": llm.get("base_url", ""),
                "has_api_key": bool(api_key),
                "api_key_masked": _mask_secret(api_key),
                "default_workspace": config.get("workspace", {}).get("default_path", ""),
                "log_level": config.get("log", {}).get("level", "INFO"),
                "sandbox_enabled": is_sandbox_enabled("default"),
            },
            request,
        )

    @application.put(f"{API_PREFIX}/settings")
    async def update_settings(payload: SettingsRequest, request: Request):
        config_manager.set_config("llm.provider", payload.provider)
        config_manager.set_config("llm.model", payload.model)
        config_manager.set_config("llm.base_url", payload.base_url)
        config_manager.set_config("workspace.default_path", payload.default_workspace)
        config_manager.set_config("log.level", payload.log_level)
        if payload.api_key is not None:
            config_manager.set_config("llm.api_key", payload.api_key)
        return await get_settings(request)

    @application.post(f"{API_PREFIX}/settings/test-connection")
    async def test_connection(request: Request):
        """使用当前生效的模型配置执行最小连接测试。"""

        from qrclaw.llm_service import get_llm_service

        try:
            response = await asyncio.to_thread(
                get_llm_service().chat,
                messages=[{"role": "user", "content": "只回复 OK"}],
                temperature=0,
            )
        except Exception as exc:
            logger.warning("模型连接测试失败：%s", exc)
            raise ApiError(
                "connection_test_failed",
                "模型连接测试失败",
                502,
                retryable=True,
                details={"reason": str(exc)},
            ) from exc
        return _success(
            {"connected": True, "response": response.content[:100]},
            request,
        )

    @application.websocket(f"{API_PREFIX}/events")
    async def events_socket(websocket: WebSocket):
        supplied = websocket.headers.get("X-QRClaw-Token") or websocket.query_params.get("token", "")
        if not hmac.compare_digest(supplied, runtime.access_token):
            await websocket.close(code=1008, reason="本地访问令牌无效")
            return
        await websocket.accept()
        await websocket.send_json(
            {
                "type": "connection.ready",
                "version": "1",
                "connection_id": f"conn_{uuid.uuid4().hex}",
            }
        )
        try:
            subscription = await websocket.receive_json()
            if subscription.get("type") != "subscribe":
                await websocket.close(code=1008, reason="第一条消息必须是 subscribe")
                return
            cursors = {
                item["run_id"]: int(item.get("after_seq", 0))
                for item in subscription.get("runs", [])
            }
            while True:
                sent = False
                for run_id, after_seq in list(cursors.items()):
                    try:
                        events = runtime.get_events(run_id, after_seq)
                    except KeyError:
                        await websocket.send_json(
                            {
                                "type": "subscription.error",
                                "run_id": run_id,
                                "code": "run_not_found",
                            }
                        )
                        del cursors[run_id]
                        continue
                    for event in events:
                        await websocket.send_json(event.model_dump(mode="json"))
                        cursors[run_id] = event.seq
                        sent = True
                if not sent:
                    await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            logger.info("WebSocket 客户端已断开")

    return application


app = create_app()
