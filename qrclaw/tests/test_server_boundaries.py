"""本机 API 的访问和输入边界。"""

from fastapi.testclient import TestClient

from qrclaw.server.app import create_app
import qrclaw.workspace as workspace_module


def test_remote_api_requires_token(temp_dir, monkeypatch):
    monkeypatch.setattr(workspace_module, "AGENTS_ROOT", temp_dir / "agents")
    app = create_app()
    with TestClient(app, client=("192.0.2.1", 50000)) as remote:
        assert remote.get("/health").status_code == 403
        monkeypatch.setenv("QRCLAW_API_TOKEN", "test-token")
        assert remote.get("/health").status_code == 403
        assert remote.get("/health", headers={"Authorization": "Bearer test-token"}).status_code == 200
    with TestClient(app, client=("127.0.0.1", 50000)) as local:
        assert local.get("/health").status_code == 200


def test_api_rejects_unsafe_session_id(temp_dir, monkeypatch):
    monkeypatch.setattr(workspace_module, "AGENTS_ROOT", temp_dir / "agents")
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        response = client.post("/v1/sessions", json={"session_id": "../outside"})
    assert response.status_code == 400
    assert not (temp_dir / "agents" / "default" / "outside.json").exists()
