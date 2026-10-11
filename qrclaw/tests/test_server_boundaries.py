"""本机 API 的访问和输入边界。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep
from unittest.mock import patch

from fastapi.testclient import TestClient

from qrclaw.server.app import create_app
from qrclaw.memory.context.session import Session, SessionEvent
from qrclaw.workspace import Workspace
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


def test_new_empty_session_survives_refresh(temp_dir, monkeypatch):
    monkeypatch.setattr(workspace_module, "AGENTS_ROOT", temp_dir / "agents")
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        created = client.post("/v1/sessions").json()
        sessions = client.get("/v1/sessions").json()
        restored = client.get(f"/v1/sessions/{created['id']}").json()

    assert created["id"] in {item["id"] for item in sessions}
    assert restored["messages"] == []


def test_session_list_has_readable_title_and_detail_has_events(temp_dir, monkeypatch):
    monkeypatch.setattr(workspace_module, "AGENTS_ROOT", temp_dir / "agents")
    workspace = Workspace()
    session = Session(workspace.sessions_dir, session_id="test-ui", resume=False)
    session.add({"role": "user", "content": "  检查项目\n架构  "})
    session.add_event(SessionEvent(kind="status", title="执行中", content="检查代码"))

    with TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        summary = next(item for item in client.get("/v1/sessions").json() if item["id"] == "test-ui")
        detail = client.get("/v1/sessions/test-ui").json()

    assert summary["title"] == "检查项目 架构"
    assert detail["events"][0]["content"] == "检查代码"


def test_api_serializes_agent_runs(temp_dir, monkeypatch):
    monkeypatch.setattr(workspace_module, "AGENTS_ROOT", temp_dir / "agents")
    active = 0
    peak = 0
    guard = Lock()

    def fake_run(**_kwargs):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        sleep(0.05)
        with guard:
            active -= 1
        return "完成"

    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run), \
         TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: client.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "你好"}],
            }), range(2)))

    assert all(response.status_code == 200 for response in results)
    assert peak == 1


def test_chat_usage_reports_this_run(temp_dir, monkeypatch):
    monkeypatch.setattr(workspace_module, "AGENTS_ROOT", temp_dir / "agents")

    def fake_run(*, session, **_kwargs):
        session.start_run_usage()
        session.update_tokens(10, 2, 12)
        session.update_tokens(15, 3, 18)
        session.add({"role": "assistant", "content": "完成"})
        return "完成"

    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run), \
         TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        response = client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "你好"}],
        })
        session_id = response.json()["session_id"]
        detail = client.get(f"/v1/sessions/{session_id}").json()

    assert response.json()["usage"] == {"prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30}
    assert detail["total_tokens"] == 30
