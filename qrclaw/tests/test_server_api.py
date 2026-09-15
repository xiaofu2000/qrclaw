"""本地工作台 HTTP 与 WebSocket 契约测试。"""

from __future__ import annotations

import time
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from qrclaw import config_manager
from qrclaw.execution.service import RunService
from qrclaw.graph.nodes import tool_runner as tool_runner_module
from qrclaw.graph.nodes.tool_runner import ToolRunner
from qrclaw.server.app import create_app
from qrclaw.workspace import Workspace


def _client(tmp_path, runner=None) -> TestClient:
    """创建使用临时存储和假 Agent 的测试客户端。"""

    def fake_runner(**kwargs):
        kwargs["execution_context"].publish("route.decided", {"route": "direct"})
        return "接口测试完成"

    workspace = Workspace(agent_id="test", _root=tmp_path / "agent")
    service = RunService(
        workspace=workspace,
        database_path=tmp_path / "runtime.sqlite3",
        agent_runner=runner or fake_runner,
    )
    return TestClient(
        create_app(service),
        headers={"X-QRClaw-Token": service.access_token},
    )


def _wait_for_status(client: TestClient, run_id: str, statuses: set[str]) -> dict:
    """轮询接口，直到运行进入期望状态。"""

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200
        snapshot = response.json()["data"]
        if snapshot["run"]["status"] in statuses:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"任务未在限定时间内进入状态：{sorted(statuses)}")


def _wait_for_approval(client: TestClient, run_id: str) -> dict:
    """轮询接口，直到运行产生待处理授权。"""

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/v1/runs/{run_id}").json()["data"]
        if snapshot["pending_approvals"]:
            return snapshot["pending_approvals"][0]
        time.sleep(0.01)
    raise AssertionError("任务未在限定时间内产生授权请求")


def test_conversation_and_run_contract(tmp_path):
    """会话、任务和事件查询应遵循统一响应格式。"""

    with _client(tmp_path) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["data"]["status"] == "ok"

        created = client.post(
            "/api/v1/conversations",
            json={"title": "接口测试", "workspace_path": str(tmp_path)},
        )
        assert created.status_code == 201
        conversation_id = created.json()["data"]["conversation_id"]

        run = client.post(
            "/api/v1/runs",
            json={
                "conversation_id": conversation_id,
                "content": "执行接口测试",
                "client_request_id": "api-request-1",
            },
        )
        assert run.status_code == 202
        run_id = run.json()["data"]["run_id"]

        snapshot = client.get(f"/api/v1/runs/{run_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["data"]["run"]["run_id"] == run_id

        _wait_for_status(client, run_id, {"completed"})
        events = client.get(f"/api/v1/runs/{run_id}/events?after_seq=0")
        assert events.status_code == 200
        assert events.json()["data"][0]["type"] == "run.started"


def test_structured_not_found_error(tmp_path):
    """错误类型必须由稳定 code 表达。"""

    with _client(tmp_path) as client:
        response = client.get("/api/v1/runs/not-exists")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"
    assert response.json()["error"]["retryable"] is False


def test_validation_error_uses_protocol_format_and_request_id(tmp_path):
    """请求校验失败时也应返回统一错误结构和请求 ID。"""

    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/runs",
            headers={"X-Request-ID": "req_from_client"},
            json={
                "conversation_id": "",
                "content": "",
                "client_request_id": "",
            },
        )

    body = response.json()
    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "req_from_client"
    assert body["request_id"] == "req_from_client"
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["errors"]


def test_invalid_workspace_uses_protocol_error(tmp_path):
    """非法工作区不能落入 FastAPI 默认错误格式。"""

    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/conversations",
            json={"title": "无效目录", "workspace_path": str(tmp_path / "missing")},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_workspace_path"


def test_commands_require_local_access_token(tmp_path):
    """除健康检查外的接口必须验证本地访问令牌。"""

    client = _client(tmp_path)
    client.headers.pop("X-QRClaw-Token")
    with client:
        response = client.get("/api/v1/conversations")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_access_token"


def test_health_does_not_require_access_token(tmp_path):
    """健康检查应允许启动器在尚未读取令牌时访问。"""

    client = _client(tmp_path)
    client.headers.pop("X-QRClaw-Token")
    with client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["data"]["service"] == "qrclaw-local-workbench"


def test_capabilities_describe_protocol_and_security_limits(tmp_path):
    """能力接口应公开前端决策所需的协议版本和限制。"""

    with _client(tmp_path) as client:
        response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["protocol_version"] == "1"
    assert "websocket_replay" in data["features"]
    assert data["limits"]["approval_decisions"] == ["allow_once", "deny"]
    assert data["limits"]["max_concurrent_runs"] == 1


def test_conversation_crud_never_deletes_workspace_files(tmp_path):
    """会话增删改查只能影响元数据，不能删除工作区内容。"""

    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    marker = workspace_path / "important.txt"
    marker.write_text("不能删除", encoding="utf-8")

    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/conversations",
            json={"title": "原标题", "workspace_path": str(workspace_path)},
        )
        conversation_id = created.json()["data"]["conversation_id"]

        updated = client.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={"title": "新标题"},
        )
        listed = client.get("/api/v1/conversations")
        deleted = client.delete(f"/api/v1/conversations/{conversation_id}")
        missing = client.get(f"/api/v1/conversations/{conversation_id}")

    assert updated.status_code == 200
    assert updated.json()["data"]["title"] == "新标题"
    assert listed.json()["data"][0]["conversation_id"] == conversation_id
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert marker.read_text(encoding="utf-8") == "不能删除"


def test_run_creation_is_idempotent_over_http(tmp_path):
    """相同 client_request_id 的重复请求应返回同一个运行。"""

    with _client(tmp_path) as client:
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "幂等测试", "workspace_path": str(tmp_path)},
        ).json()["data"]
        payload = {
            "conversation_id": conversation["conversation_id"],
            "content": "只执行一次",
            "client_request_id": "same-http-request",
        }
        first = client.post("/api/v1/runs", json=payload)
        second = client.post("/api/v1/runs", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["run_id"] == second.json()["data"]["run_id"]


def test_event_query_returns_only_items_after_cursor(tmp_path):
    """事件查询必须严格返回 after_seq 之后的事件。"""

    with _client(tmp_path) as client:
        conversation_id = client.post(
            "/api/v1/conversations",
            json={"title": "游标测试", "workspace_path": str(tmp_path)},
        ).json()["data"]["conversation_id"]
        run_id = client.post(
            "/api/v1/runs",
            json={
                "conversation_id": conversation_id,
                "content": "生成事件",
                "client_request_id": "cursor-request",
            },
        ).json()["data"]["run_id"]
        snapshot = _wait_for_status(client, run_id, {"completed"})
        events = client.get(f"/api/v1/runs/{run_id}/events?after_seq=2").json()["data"]

    assert events
    assert all(event["seq"] > 2 for event in events)
    assert [event["seq"] for event in events] == list(
        range(3, snapshot["run"]["last_seq"] + 1)
    )


def test_active_conversation_cannot_be_deleted(tmp_path):
    """存在活动运行时删除会话应返回可识别的冲突错误。"""

    release = threading.Event()

    def blocking_runner(**kwargs):
        """保持任务运行，直到测试允许其结束。"""

        while not release.wait(0.01):
            kwargs["execution_context"].check_cancelled()
        return "完成"

    try:
        with _client(tmp_path, blocking_runner) as client:
            conversation_id = client.post(
                "/api/v1/conversations",
                json={"title": "活动会话", "workspace_path": str(tmp_path)},
            ).json()["data"]["conversation_id"]
            run_id = client.post(
                "/api/v1/runs",
                json={
                    "conversation_id": conversation_id,
                    "content": "保持运行",
                    "client_request_id": "active-run",
                },
            ).json()["data"]["run_id"]
            _wait_for_status(client, run_id, {"running"})

            response = client.delete(f"/api/v1/conversations/{conversation_id}")

            assert response.status_code == 409
            assert response.json()["error"]["code"] == "conversation_has_active_run"
    finally:
        release.set()


def test_cancel_endpoint_drives_run_to_cancelled(tmp_path):
    """取消接口应先接收请求，再由执行线程发布最终取消事件。"""

    def blocking_runner(**kwargs):
        """持续检查取消令牌的测试运行器。"""

        while True:
            kwargs["execution_context"].check_cancelled()
            time.sleep(0.01)

    with _client(tmp_path, blocking_runner) as client:
        conversation_id = client.post(
            "/api/v1/conversations",
            json={"title": "取消接口", "workspace_path": str(tmp_path)},
        ).json()["data"]["conversation_id"]
        run_id = client.post(
            "/api/v1/runs",
            json={
                "conversation_id": conversation_id,
                "content": "持续执行",
                "client_request_id": "cancel-api",
            },
        ).json()["data"]["run_id"]
        _wait_for_status(client, run_id, {"running"})

        accepted = client.post(
            f"/api/v1/runs/{run_id}/cancel",
            json={"reason": "user_requested"},
        )
        cancelled = _wait_for_status(client, run_id, {"cancelled"})
        events = client.get(f"/api/v1/runs/{run_id}/events").json()["data"]

    assert accepted.status_code == 202
    assert accepted.json()["data"]["status"] in {"cancelling", "cancelled"}
    assert cancelled["run"]["status"] == "cancelled"
    assert [event["type"] for event in events][-1] == "run.cancelled"


def test_approval_endpoint_is_idempotent_and_honors_denial(tmp_path, monkeypatch):
    """授权接口重复调用时应返回首次决定，且拒绝后不执行工具。"""

    executed = []
    monkeypatch.setattr(tool_runner_module, "need_confirm", lambda _: True)
    monkeypatch.setattr(
        tool_runner_module,
        "execute",
        lambda *_: executed.append(True) or "不应执行",
    )

    def approval_runner(**kwargs):
        """执行一次需要前端授权的危险工具。"""

        return ToolRunner(execution_context=kwargs["execution_context"]).run(
            "run_shell",
            '{"command":"echo test"}',
        )

    with _client(tmp_path, approval_runner) as client:
        conversation_id = client.post(
            "/api/v1/conversations",
            json={"title": "授权接口", "workspace_path": str(tmp_path)},
        ).json()["data"]["conversation_id"]
        run_id = client.post(
            "/api/v1/runs",
            json={
                "conversation_id": conversation_id,
                "content": "执行危险工具",
                "client_request_id": "approval-api",
            },
        ).json()["data"]["run_id"]
        approval = _wait_for_approval(client, run_id)

        first = client.post(
            f"/api/v1/tool-approvals/{approval['approval_id']}/resolve",
            json={"decision": "deny"},
        )
        second = client.post(
            f"/api/v1/tool-approvals/{approval['approval_id']}/resolve",
            json={"decision": "allow_once"},
        )
        completed = _wait_for_status(client, run_id, {"completed"})

    assert first.status_code == 200
    assert first.json()["data"] == second.json()["data"] == {
        "approval_id": approval["approval_id"],
        "decision": "deny",
    }
    assert executed == []
    assert completed["tool_calls"][0]["status"] == "denied"


def test_settings_mask_api_key_without_returning_secret(tmp_path, monkeypatch):
    """设置查询只能返回 API Key 掩码，不能泄露完整密钥。"""

    secret = "sk-test-super-secret-1234"
    monkeypatch.setattr(
        config_manager,
        "get_config",
        lambda: {
            "llm": {
                "provider": "litellm",
                "model": "openai/test-model",
                "base_url": "https://example.test/v1",
                "api_key": secret,
            },
            "workspace": {"default_path": str(tmp_path)},
            "log": {"level": "INFO"},
        },
    )
    monkeypatch.setattr("qrclaw.sandbox.is_sandbox_enabled", lambda _: True)

    with _client(tmp_path) as client:
        response = client.get("/api/v1/settings")

    body_text = response.text
    data = response.json()["data"]
    assert response.status_code == 200
    assert data["has_api_key"] is True
    assert data["api_key_masked"] == "••••••••1234"
    assert data["sandbox_enabled"] is True
    assert secret not in body_text
    assert "api_key" not in data


def test_connection_test_maps_provider_failure_to_retryable_error(tmp_path, monkeypatch):
    """模型连接失败应返回稳定且可重试的协议错误。"""

    class _UnavailableService:
        """模拟不可用模型服务。"""

        def chat(self, **_):
            raise RuntimeError("上游连接超时")

    monkeypatch.setattr(
        "qrclaw.llm_service.get_llm_service",
        lambda: _UnavailableService(),
    )

    with _client(tmp_path) as client:
        response = client.post("/api/v1/settings/test-connection")

    body = response.json()
    assert response.status_code == 502
    assert body["error"]["code"] == "connection_test_failed"
    assert body["error"]["retryable"] is True
    assert body["error"]["details"]["reason"] == "上游连接超时"


def test_connection_test_returns_bounded_preview(tmp_path, monkeypatch):
    """模型连接成功时只返回有限长度的响应预览。"""

    service = SimpleNamespace(
        chat=lambda **_: SimpleNamespace(content="好" * 120),
    )
    monkeypatch.setattr("qrclaw.llm_service.get_llm_service", lambda: service)

    with _client(tmp_path) as client:
        response = client.post("/api/v1/settings/test-connection")

    data = response.json()["data"]
    assert response.status_code == 200
    assert data["connected"] is True
    assert len(data["response"]) == 100


def test_openai_compatibility_routes_are_removed(tmp_path):
    """彻底改造后不能继续暴露旧 OpenAI 兼容接口。"""

    with _client(tmp_path) as client:
        assert client.get("/v1/models").status_code == 404
        assert client.post("/v1/chat/completions", json={}).status_code == 404


def test_websocket_replays_events(tmp_path):
    """WebSocket 订阅应从 after_seq 之后补发事件。"""

    with _client(tmp_path) as client:
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "WebSocket 测试", "workspace_path": str(tmp_path)},
        ).json()["data"]
        run_id = client.post(
            "/api/v1/runs",
            json={
                "conversation_id": conversation["conversation_id"],
                "content": "测试补发",
                "client_request_id": "ws-request-1",
            },
        ).json()["data"]["run_id"]

        with client.websocket_connect("/api/v1/events") as socket:
            ready = socket.receive_json()
            assert ready["type"] == "connection.ready"
            socket.send_json(
                {"type": "subscribe", "runs": [{"run_id": run_id, "after_seq": 0}]}
            )
            first_event = socket.receive_json()
            assert first_event["run_id"] == run_id
            assert first_event["seq"] == 1


def test_websocket_reports_missing_run_subscription(tmp_path):
    """订阅不存在的运行时应发送结构化订阅错误。"""

    with _client(tmp_path) as client:
        with client.websocket_connect("/api/v1/events") as socket:
            assert socket.receive_json()["type"] == "connection.ready"
            socket.send_json(
                {
                    "type": "subscribe",
                    "runs": [{"run_id": "run_missing", "after_seq": 0}],
                }
            )
            error = socket.receive_json()

    assert error == {
        "type": "subscription.error",
        "run_id": "run_missing",
        "code": "run_not_found",
    }
