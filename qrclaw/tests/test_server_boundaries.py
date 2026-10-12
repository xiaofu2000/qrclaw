"""本机 API 的访问和输入边界。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, Thread
from time import sleep
import re
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import uvicorn

from qrclaw.server.app import create_app
from qrclaw.memory.context.session import Session, SessionEvent
from qrclaw.workspace import Workspace
import qrclaw.workspace as workspace_module


def test_packaged_ui_references_available_assets(temp_dir):
    with TestClient(create_app(Workspace(_root=temp_dir)), client=("127.0.0.1", 50000)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert 'lang="zh-CN"' in response.text
        assets = re.findall(r'(?:src|href)="(/static/[^\"]+)"', response.text)
        assert any(asset.endswith(".js") for asset in assets)
        assert any(asset.endswith(".css") for asset in assets)
        for asset in assets:
            assert client.get(asset).status_code == 200


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


def test_read_missing_session_returns_not_found(temp_dir):
    """读取不存在的会话不会伪装成空会话。"""
    with TestClient(create_app(Workspace(_root=temp_dir)), client=("127.0.0.1", 50000)) as client:
        assert client.get("/v1/sessions/missing").status_code == 404


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


def test_session_rename_persists_for_empty_and_existing_conversations(temp_dir):
    """会话标题由持久化字段提供，空会话也可改名。"""
    workspace = Workspace(_root=temp_dir)
    with TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        session_id = client.post("/v1/sessions").json()["id"]
        path = f"/v1/sessions/{session_id}"
        assert client.patch(path, json={"title": "  项目讨论  "}).json()["title"] == "项目讨论"
        assert next(s for s in client.get("/v1/sessions").json() if s["id"] == session_id)["title"] == "项目讨论"
        assert client.get(path).json()["title"] == "项目讨论"
        assert client.patch(path, json={"title": "   "}).status_code == 400
        assert client.patch("/v1/sessions/missing", json={"title": "新标题"}).status_code == 404


def test_delete_latest_turn_removes_replies_events_and_child_session(temp_dir):
    """删除末轮时同步清理该轮回复、过程和子任务会话。"""
    workspace = Workspace(_root=temp_dir)
    parent = Session(workspace.sessions_dir, session_id="parent")
    parent.add({"role": "user", "content": "第一轮"})
    parent.add_event({"kind": "status", "title": "执行中", "content": "保留"})
    parent.add({"role": "assistant", "content": "第一轮回复"})
    parent.add({"role": "user", "content": "第二轮"})
    last_id = parent.messages[-1]["uuid"]
    child = Session(workspace.sessions_dir, session_id="sub-worker-delete")
    child.ensure_saved()
    parent.add_event({"kind": "subagent", "title": "子任务", "meta": {"child_session_id": child.session_id}})
    parent.add({"role": "assistant", "content": "第二轮回复"})
    with TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        assert client.delete("/v1/sessions/parent/messages/stale").status_code == 409
        result = client.delete(f"/v1/sessions/parent/messages/{last_id}")
        assert result.status_code == 200
        assert [m["content"] for m in result.json()["messages"]] == ["第一轮", "第一轮回复"]
        assert [e["content"] for e in result.json()["events"]] == ["保留"]
        assert result.json()["history_version"] == 1
        assert client.delete(f"/v1/sessions/parent/messages/{last_id}").status_code == 409
    assert not child._path.exists()


def test_edit_latest_user_message_reruns_once_without_old_reply(temp_dir):
    """编辑请求保留原消息 ID，旧回复被替换且 Agent 只执行一次。"""
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="edit")
    session.add({"role": "user", "content": "旧问题"})
    message_id = session.messages[-1]["uuid"]
    session.add({"role": "assistant", "content": "旧回复"})
    calls = []

    def fake_run(*, session, user_input, record_user_message, on_delta, **_kwargs):
        calls.append((user_input, record_user_message))
        on_delta("新")
        session.add({"role": "assistant", "content": "新回复"})
        return "新回复"

    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run), \
         TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        response = client.post("/v1/chat/completions", json={"session_id": "edit", "edit_message_id": message_id,
            "stream": True, "messages": [{"role": "user", "content": "新问题"}]})
        saved = client.get("/v1/sessions/edit").json()
        assert client.post("/v1/chat/completions", json={"session_id": "edit", "edit_message_id": "wrong",
            "messages": [{"role": "user", "content": "覆盖"}]}).status_code == 409
    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert calls == [("新问题", False)]
    assert [(m["role"], m["content"]) for m in saved["messages"]] == [("user", "新问题"), ("assistant", "新回复")]
    assert saved["messages"][0]["uuid"] == message_id
    assert saved["history_version"] == 1


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


def test_running_session_is_visible_and_cannot_be_submitted_or_deleted(temp_dir):
    started, release = Event(), Event()

    def fake_run(**_kwargs):
        started.set()
        assert release.wait(3)
        return "完成"

    workspace = Workspace(_root=temp_dir)
    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run), \
         TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client, \
         ThreadPoolExecutor(max_workers=1) as pool:
        session_id = client.post("/v1/sessions").json()["id"]
        existing = Session(workspace.sessions_dir, session_id)
        existing.add({"role": "user", "content": "已有问题"})
        message_id = existing.messages[-1]["uuid"]
        request = {"session_id": session_id, "messages": [{"role": "user", "content": "执行一次"}]}
        future = pool.submit(client.post, "/v1/chat/completions", json=request)
        try:
            assert started.wait(2)
            assert client.get(f"/v1/sessions/{session_id}").json()["run_status"] == "running"
            summary = next(item for item in client.get("/v1/sessions").json() if item["id"] == session_id)
            assert summary["run_status"] == "running"
            assert client.post("/v1/chat/completions", json=request).status_code == 409
            assert client.post("/v1/chat/completions", json={**request, "stream": True}).status_code == 409
            assert client.delete(f"/v1/sessions/{session_id}").status_code == 409
            assert client.delete(f"/v1/sessions/{session_id}/messages/{message_id}").status_code == 409
            assert client.post("/v1/chat/completions", json={**request, "edit_message_id": message_id}).status_code == 409
        finally:
            release.set()
        assert future.result().status_code == 200
        assert client.get(f"/v1/sessions/{session_id}").json()["run_status"] == "idle"


def test_failed_run_remains_visible_until_retry(temp_dir):
    with TestClient(create_app(Workspace(_root=temp_dir)), client=("127.0.0.1", 50000)) as client:
        session_id = client.post("/v1/sessions").json()["id"]
        request = {"session_id": session_id, "messages": [{"role": "user", "content": "执行"}]}
        with patch("qrclaw.server.routes.run_agent", side_effect=RuntimeError("model unavailable")):
            assert client.post("/v1/chat/completions", json=request).status_code == 500
        detail = client.get(f"/v1/sessions/{session_id}").json()
        assert detail["run_status"] == "idle"
        assert detail["run_error"] == "model unavailable"
        with patch("qrclaw.server.routes.run_agent", return_value="完成"):
            assert client.post("/v1/chat/completions", json=request).status_code == 200
        assert client.get(f"/v1/sessions/{session_id}").json()["run_error"] is None


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


def test_chat_passes_plan_switch_for_stream_and_json(temp_dir):
    """流式与非流式请求均把 Plan 开关交给同一个 Agent 入口。"""
    calls = []

    def fake_run(*, plan_enabled, **_kwargs):
        """记录请求的开关值，避免调用真实模型。"""
        calls.append(plan_enabled)
        return "完成"

    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run), \
         TestClient(create_app(Workspace(_root=temp_dir)), client=("127.0.0.1", 50000)) as client:
        for stream, enabled in [(False, False), (True, True)]:
            response = client.post("/v1/chat/completions", json={"stream": stream, "plan_enabled": enabled,
                "messages": [{"role": "user", "content": "执行任务"}]})
            assert response.status_code == 200
    assert calls == [False, True]


def test_stream_chat_sends_chunks_and_keeps_saved_session(temp_dir):
    """流式正文与最终会话内容都来自同一次 Agent 执行。"""
    workspace = Workspace(_root=temp_dir)
    def fake_run(*, session, on_delta, **_kwargs):
        session.add({"role": "user", "content": "你好"})
        on_delta("你")
        on_delta("好")
        session.add({"role": "assistant", "content": "你好"})
        return "你好"

    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run), \
         TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        response = client.post("/v1/chat/completions", json={"stream": True, "messages": [{"role": "user", "content": "你好"}]})
        session_id = response.headers["x-qrclaw-session-id"]
        saved = client.get(f"/v1/sessions/{session_id}").json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
    assert all(frame["object"] == "chat.completion.chunk" for frame in frames)
    assert [frame["choices"][0]["delta"].get("content") for frame in frames] == ["", "你", "好", None]
    assert frames[-1]["choices"][0]["finish_reason"] == "stop"
    assert "data: [DONE]" in response.text
    assert saved["messages"][-1]["content"] == "你好"


def test_progress_stream_replays_only_status_events(temp_dir):
    """续接时不把工具参数、结果或执行汇报正文直播出去。"""
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="progress", resume=False)
    session.add_event(SessionEvent(kind="status", title="执行中", content="检查代码"))
    session.add_event(SessionEvent(kind="tool_status", title="工具开始", content="read_file", meta={"state": "started"}))
    session.add_event(SessionEvent(kind="execution_report", title="执行汇报", content="敏感结果"))
    session.add_event(SessionEvent(kind="status", title="规划执行失败", content="内部错误详情"))
    with TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        response = client.get("/v1/sessions/progress/events/stream?after=0")
    assert "id: 1" in response.text
    assert "read_file" in response.text
    assert "敏感结果" not in response.text
    assert "内部错误详情" not in response.text
    assert '"state": "started"' in response.text
    assert "id: 0" not in response.text


def test_stream_error_keeps_failure_state_and_does_not_retry(temp_dir):
    """部分片段后的异常留在会话状态中，流式请求只执行一次。"""
    calls = 0
    def fake_run(*, on_delta, **_kwargs):
        nonlocal calls
        calls += 1
        on_delta("前半段")
        raise RuntimeError("模型中断")

    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run), \
         TestClient(create_app(Workspace(_root=temp_dir)), client=("127.0.0.1", 50000)) as client:
        session_id = client.post("/v1/sessions").json()["id"]
        response = client.post("/v1/chat/completions", json={"stream": True, "session_id": session_id,
            "messages": [{"role": "user", "content": "执行"}]})
        saved = client.get(f"/v1/sessions/{session_id}").json()
    assert calls == 1
    assert "前半段" in response.text
    assert '"error"' in response.text
    assert saved["run_status"] == "idle"
    assert saved["run_error"] == "模型中断"


def test_stream_disconnect_does_not_stop_agent(temp_dir):
    """HTTP 客户端提前断开后，后台 Agent 仍保存最终会话。"""
    started, release, finished = Event(), Event(), Event()

    def fake_run(*, session, on_delta, **_kwargs):
        session.add({"role": "user", "content": "继续完成"})
        on_delta("开")
        started.set()
        assert release.wait(3)
        on_delta("始")
        session.add({"role": "assistant", "content": "完成"})
        finished.set()
        return "完成"

    app = create_app(Workspace(_root=temp_dir))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run):
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                sleep(0.01)
            assert server.started
            port = server.servers[0].sockets[0].getsockname()[1]
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                session_id = client.post("/v1/sessions").json()["id"]
                with client.stream("POST", "/v1/chat/completions", json={"stream": True, "session_id": session_id,
                        "messages": [{"role": "user", "content": "继续完成"}]}) as response:
                    assert response.status_code == 200
                    assert next(response.iter_lines()).startswith("data: ")
                    assert started.wait(2)
                assert client.get(f"/v1/sessions/{session_id}").json()["run_status"] == "running"
                release.set()
                assert finished.wait(2)
                for _ in range(100):
                    saved = client.get(f"/v1/sessions/{session_id}").json()
                    if saved["run_status"] == "idle":
                        break
                    sleep(0.01)
                assert saved["messages"][-1]["content"] == "完成"
                assert saved["run_status"] == "idle"
        finally:
            release.set()
            server.should_exit = True
            thread.join(timeout=3)
