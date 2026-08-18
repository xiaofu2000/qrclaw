"""本地工作台 HTTP 与 WebSocket 契约测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from qrclaw.execution.service import RunService
from qrclaw.server.app import create_app
from qrclaw.workspace import Workspace


def _client(tmp_path) -> TestClient:
    """创建使用临时存储和假 Agent 的测试客户端。"""

    def fake_runner(**kwargs):
        kwargs["execution_context"].publish("route.decided", {"route": "direct"})
        return "接口测试完成"

    workspace = Workspace(agent_id="test", _root=tmp_path / "agent")
    service = RunService(
        workspace=workspace,
        database_path=tmp_path / "runtime.sqlite3",
        agent_runner=fake_runner,
    )
    return TestClient(
        create_app(service),
        headers={"X-QRClaw-Token": service.access_token},
    )


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
