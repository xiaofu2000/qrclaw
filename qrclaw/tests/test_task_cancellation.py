"""任务停止/取消机制（CancelToken、Shell 子进程终止、子 Agent 级联取消与 API 闭环）测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import BoundedSemaphore, Event, Thread
import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from rich.console import Console

from qrclaw.agent import set_session, set_workspace
from qrclaw.cancellation import CancelToken, TaskCancelledError, bind_cancel_token, check_cancelled, get_cancel_token
from qrclaw.graph.nodes.plan_executor import PlanExecutorNode
from qrclaw.graph.nodes.react_loop import ReactLoopNode
from qrclaw.graph.nodes.router import PlanStep
from qrclaw.memory.context.runtime.manager import init_context_manager
from qrclaw.memory.context.session import Session
from qrclaw.memory.context.session.events import status_code
from qrclaw.providers.base import LLMResponse, ToolCall
from qrclaw.providers.litellm_provider import LiteLLMProvider
from qrclaw.server import routes
from qrclaw.server.app import create_app
from qrclaw.subagent_progress import has_active_sub_agents
from qrclaw.tools import shell as shell_tool
from qrclaw.tools import spawn_agent as spawn_tool
from qrclaw.tools import wait_agents as wait_tool
from qrclaw.workspace import Workspace


def _make_client(workspace: Workspace) -> TestClient:
    return TestClient(create_app(workspace), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def test_litellm_provider_propagates_task_cancelled_error_directly():
    """LiteLLMProvider 在重试等待与流式迭代中被取消时原样抛出 TaskCancelledError，并关闭底层流。"""
    from litellm.exceptions import APIConnectionError

    provider = LiteLLMProvider()

    # 1. 重试等待期间取消：不包装为 RuntimeError，且立即中断 sleep
    retry_token = CancelToken()
    entered_retry = Event()

    def failing_completion(**_kwargs):
        entered_retry.set()
        raise APIConnectionError(message="network down", llm_provider="openai", model="gpt-4o")

    def cancel_soon():
        assert entered_retry.wait(2)
        time.sleep(0.05)
        retry_token.cancel()

    canceller = Thread(target=cancel_soon, daemon=True)
    canceller.start()
    start = time.monotonic()
    with patch("qrclaw.providers.litellm_provider.completion", side_effect=failing_completion), \
         bind_cancel_token(retry_token):
        with pytest.raises(TaskCancelledError):
            provider.chat([{"role": "user", "content": "你好"}])
    assert time.monotonic() - start < 1.0
    canceller.join(timeout=1)

    # 2. 流式输出中途取消：关闭底层流迭代器并原样向上抛出 TaskCancelledError
    stream_token = CancelToken()
    closed = False

    class ClosableStream:
        def __init__(self):
            self._index = 0

        def __iter__(self):
            return self

        def __next__(self):
            self._index += 1
            if self._index == 1:
                delta = SimpleNamespace(content="第一块", tool_calls=None, reasoning_content=None)
                choice = SimpleNamespace(delta=delta, finish_reason=None)
                return SimpleNamespace(choices=[choice], usage=None)
            stream_token.cancel()
            delta = SimpleNamespace(content="第二块", tool_calls=None, reasoning_content=None)
            choice = SimpleNamespace(delta=delta, finish_reason=None)
            return SimpleNamespace(choices=[choice], usage=None)

        def close(self):
            nonlocal closed
            closed = True

    received: list[str] = []
    with patch("qrclaw.providers.litellm_provider.completion", return_value=ClosableStream()), \
         bind_cancel_token(stream_token):
        with pytest.raises(TaskCancelledError):
            provider.chat([{"role": "user", "content": "流式测试"}], on_delta=received.append)

    assert received == ["第一块"]
    assert closed is True


def test_react_loop_cancels_tool_without_continuing_next_llm_turn(temp_dir):
    """工具执行中被取消时记录 cancelled 状态并补齐配对消息，绝不吞掉异常继续调下一轮 LLM。"""
    workspace = Workspace("worker", _root=temp_dir)
    session = Session(workspace.sessions_dir, resume=False)
    session.add({"role": "user", "content": "执行两个工具"})
    init_context_manager(session, workspace, is_sub_agent=True)

    token = CancelToken()
    llm_calls = 0
    executed_tools: list[str] = []

    class FakeLLM:
        def chat(self, *_args, **_kwargs):
            nonlocal llm_calls
            llm_calls += 1
            if llm_calls == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall("call-1", "slow_tool", "{}"),
                        ToolCall("call-2", "second_tool", "{}"),
                    ],
                    finish_reason="tool_calls",
                )
            pytest.fail("取消后不应继续执行下一轮 LLM 调用")

    def fake_execute(name: str, _arguments: str) -> str:
        executed_tools.append(name)
        token.cancel()
        check_cancelled()
        return "不应到达"

    node = ReactLoopNode()
    with patch("qrclaw.graph.nodes.react_loop.get_llm_service", return_value=FakeLLM()), \
         patch("qrclaw.graph.nodes.tool_runner.execute", side_effect=fake_execute), \
         patch.object(ReactLoopNode, "_get_memory_integration", return_value=None), \
         bind_cancel_token(token):
        with pytest.raises(TaskCancelledError):
            node.run(session, Console(file=StringIO()), workspace, auto_confirm=True, is_sub_agent=False)

    assert llm_calls == 1
    assert executed_tools == ["slow_tool"]
    restored = Session(workspace.sessions_dir, session.session_id)
    tool_events = [e for e in restored.events if e["kind"] == "tool_status"]
    assert [e["meta"]["state"] for e in tool_events] == ["started", "cancelled"]
    tool_msgs = [m for m in restored.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["call-1", "call-2"]


def test_shell_exec_direct_kills_long_running_subprocess_within_one_second(temp_dir):
    """_exec_direct 执行长时间 Shell 子进程时触发 CancelToken.cancel() 能在 1 秒内杀掉进程组并抛 TaskCancelledError。"""
    token = CancelToken()
    errors: list[BaseException] = []

    def run_sleep():
        with bind_cancel_token(token):
            try:
                shell_tool._exec_direct("sleep 30", cwd=str(temp_dir))
            except BaseException as exc:
                errors.append(exc)

    worker = Thread(target=run_sleep, daemon=True)
    worker.start()

    # 等待子进程已注册到 CancelToken
    for _ in range(50):
        if token._procs:
            break
        time.sleep(0.02)
    assert len(token._procs) == 1
    proc = next(iter(token._procs))

    started_at = time.monotonic()
    assert token.cancel() is True
    worker.join(timeout=1.5)
    elapsed = time.monotonic() - started_at

    assert not worker.is_alive(), "Shell 工作线程未在超时时间内退出"
    assert elapsed < 1.0, f"终止子进程耗时过长: {elapsed:.3f}s"
    assert len(errors) == 1 and isinstance(errors[0], TaskCancelledError)
    assert proc.poll() is not None
    assert len(token._procs) == 0


def test_cancel_running_stream_records_run_cancelled_and_no_error(temp_dir):
    """流式输出阻塞时调用 POST /v1/sessions/{id}/cancel 迅速中断，保存部分正文与 run_cancelled 事件且不写 run_error。"""
    workspace = Workspace(_root=temp_dir)
    streaming_started = Event()

    def fake_run(*, session, user_input, on_delta=None, record_user_message=True, **_kwargs):
        if record_user_message:
            session.add({"role": "user", "content": user_input})
        if on_delta:
            on_delta("已生成的半截回复")
        streaming_started.set()
        token = get_cancel_token()
        assert token is not None
        token.wait(timeout=5.0)
        check_cancelled()
        return "完整回复"

    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run), \
         _make_client(workspace) as client, \
         ThreadPoolExecutor(max_workers=2) as pool:
        assert client.post("/v1/sessions/not-exists/cancel").status_code == 404

        session_id = client.post("/v1/sessions").json()["id"]
        # 空闲会话幂等返回 cancelled=False
        idle_res = client.post(f"/v1/sessions/{session_id}/cancel").json()
        assert idle_res == {"cancelled": False, "session_id": session_id, "run_status": "idle"}

        stream_future = pool.submit(
            client.post,
            "/v1/chat/completions",
            json={"session_id": session_id, "stream": True, "messages": [{"role": "user", "content": "写一篇长文"}]},
        )
        assert streaming_started.wait(timeout=2.0)

        t0 = time.monotonic()
        cancel_res = client.post(f"/v1/sessions/{session_id}/cancel")
        elapsed = time.monotonic() - t0
        assert cancel_res.status_code == 200
        assert elapsed < 1.0
        assert cancel_res.json() == {"cancelled": True, "session_id": session_id, "run_status": "idle"}

        stream_resp = stream_future.result(timeout=2.0)
        assert stream_resp.status_code == 200
        assert "已生成的半截回复" in stream_resp.text
        assert '"finish_reason": "stop"' in stream_resp.text
        assert "data: [DONE]" in stream_resp.text
        assert '"error"' not in stream_resp.text

        detail = client.get(f"/v1/sessions/{session_id}").json()
        assert detail["run_status"] == "idle"
        assert detail["run_error"] is None
        assert detail["active_subagents"] is False
        assert [(m["role"], m["content"]) for m in detail["messages"]] == [
            ("user", "写一篇长文"),
            ("assistant", "已生成的半截回复"),
        ]
        codes = [status_code(e) for e in detail["events"]]
        assert "run_cancelled" in codes


def test_cancel_immediately_allows_session_delete_and_turn_edit_or_delete(temp_dir):
    """取消完成后立即删除会话或编辑/删除最后一轮用户消息不会再报 409。"""
    workspace = Workspace(_root=temp_dir)
    entered = Event()

    def slow_or_fast_run(*, session, user_input, record_user_message=True, **_kwargs):
        if record_user_message:
            session.add({"role": "user", "content": user_input})
        if user_input.startswith("慢任务"):
            entered.set()
            token = get_cancel_token()
            assert token is not None
            token.wait(timeout=5.0)
            check_cancelled()
        session.add({"role": "assistant", "content": f"正常完成：{user_input}"})
        return f"正常完成：{user_input}"

    with patch("qrclaw.server.routes.run_agent", side_effect=slow_or_fast_run), \
         _make_client(workspace) as client, \
         ThreadPoolExecutor(max_workers=2) as pool:
        # 1. 取消后立即 DELETE 会话
        s_del = client.post("/v1/sessions").json()["id"]
        fut_del = pool.submit(
            client.post,
            "/v1/chat/completions",
            json={"session_id": s_del, "messages": [{"role": "user", "content": "慢任务-待删会话"}]},
        )
        assert entered.wait(timeout=2.0)
        assert client.delete(f"/v1/sessions/{s_del}").status_code == 409
        assert client.post(f"/v1/sessions/{s_del}/cancel").status_code == 200
        del_resp = client.delete(f"/v1/sessions/{s_del}")
        assert del_resp.status_code == 200 and del_resp.json() == {"deleted": True}
        assert fut_del.result(timeout=2.0).status_code == 200

        # 2. 取消后立即编辑末轮消息，以及再次取消后立即删除末轮消息
        entered.clear()
        s_turn = client.post("/v1/sessions").json()["id"]
        fut_turn = pool.submit(
            client.post,
            "/v1/chat/completions",
            json={"session_id": s_turn, "stream": True, "messages": [{"role": "user", "content": "慢任务-首轮"}]},
        )
        assert entered.wait(timeout=2.0)
        assert client.post(f"/v1/sessions/{s_turn}/cancel").status_code == 200
        fut_turn.result(timeout=2.0)

        detail = client.get(f"/v1/sessions/{s_turn}").json()
        user_msg_id = detail["messages"][0]["uuid"]

        # 立即编辑末轮消息重跑快速任务，不报 409
        edit_resp = client.post(
            "/v1/chat/completions",
            json={
                "session_id": s_turn,
                "edit_message_id": user_msg_id,
                "messages": [{"role": "user", "content": "快任务-重跑"}],
            },
        )
        assert edit_resp.status_code == 200
        edited = client.get(f"/v1/sessions/{s_turn}").json()
        assert [(m["role"], m["content"]) for m in edited["messages"]] == [
            ("user", "快任务-重跑"),
            ("assistant", "正常完成：快任务-重跑"),
        ]

        # 再次发起慢任务并取消，随后立即删除末轮消息，不报 409
        entered.clear()
        fut_turn2 = pool.submit(
            client.post,
            "/v1/chat/completions",
            json={"session_id": s_turn, "messages": [{"role": "user", "content": "慢任务-第二轮"}]},
        )
        assert entered.wait(timeout=2.0)
        assert client.post(f"/v1/sessions/{s_turn}/cancel").status_code == 200
        fut_turn2.result(timeout=2.0)

        detail2 = client.get(f"/v1/sessions/{s_turn}").json()
        second_user_id = [m for m in detail2["messages"] if m["role"] == "user"][-1]["uuid"]
        delete_msg_resp = client.delete(f"/v1/sessions/{s_turn}/messages/{second_user_id}")
        assert delete_msg_resp.status_code == 200
        assert [m["content"] for m in delete_msg_resp.json()["messages"]] == ["快任务-重跑", "正常完成：快任务-重跑"]


def test_cancel_queued_session_skips_llm_execution(temp_dir, monkeypatch):
    """排队中（queued）的任务被取消后立即释放且不会再执行 run_agent / LLM。"""
    monkeypatch.setattr(routes, "_run_slots", BoundedSemaphore(1))
    workspace = Workspace(_root=temp_dir)
    first_started = Event()
    release_first = Event()
    executed_sessions: list[str] = []

    def fake_run(*, session, user_input, record_user_message=True, **_kwargs):
        executed_sessions.append(session.session_id)
        if record_user_message:
            session.add({"role": "user", "content": user_input})
        if session.session_id == "first":
            first_started.set()
            release_first.wait(timeout=5.0)
        session.add({"role": "assistant", "content": "完成"})
        return "完成"

    with patch("qrclaw.server.routes.run_agent", side_effect=fake_run), \
         _make_client(workspace) as client, \
         ThreadPoolExecutor(max_workers=3) as pool:
        client.post("/v1/sessions", json={"session_id": "first"})
        client.post("/v1/sessions", json={"session_id": "second"})

        fut_first = pool.submit(
            client.post,
            "/v1/chat/completions",
            json={"session_id": "first", "messages": [{"role": "user", "content": "任务一"}]},
        )
        assert first_started.wait(timeout=2.0)

        fut_second = pool.submit(
            client.post,
            "/v1/chat/completions",
            json={"session_id": "second", "messages": [{"role": "user", "content": "排队任务二"}]},
        )
        for _ in range(50):
            if client.get("/v1/sessions/second").json()["run_status"] == "queued":
                break
            time.sleep(0.02)
        assert client.get("/v1/sessions/second").json()["run_status"] == "queued"

        # 在 first 仍占满槽位时取消 second，无需等待 first 完成即可立即回到 idle
        cancel_resp = client.post("/v1/sessions/second/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json() == {"cancelled": True, "session_id": "second", "run_status": "idle"}
        assert fut_second.result(timeout=1.0).status_code == 200

        second_detail = client.get("/v1/sessions/second").json()
        assert second_detail["run_status"] == "idle"
        assert second_detail["run_error"] is None
        assert [m["content"] for m in second_detail["messages"]] == ["排队任务二"]
        assert any(status_code(e) == "run_cancelled" for e in second_detail["events"])

        # 释放 first，确认 second 始终没有进入 run_agent
        release_first.set()
        assert fut_first.result(timeout=2.0).status_code == 200

    assert executed_sessions == ["first"]


def test_cancel_parent_session_cleans_up_spawned_and_plan_subagents(temp_dir):
    """子 Agent（spawn_agent 后台线程与 Plan 并行步骤）运行期间取消父会话后，子任务状态与 active_subagents 均正确清理。"""
    workspace = Workspace(_root=temp_dir)

    # 1. API + spawn_agent + wait_agents 场景
    sub_started = Event()

    def fake_sub_run(_task, _ws, _agent_id, **_kwargs):
        sub_started.set()
        token = get_cancel_token()
        assert token is not None
        token.wait(timeout=5.0)
        check_cancelled()
        return "子任务完成", None

    def parent_with_spawn(*, session, user_input, record_user_message=True, **_kwargs):
        if record_user_message:
            session.add({"role": "user", "content": user_input})
        set_session(session)
        set_workspace(workspace)
        try:
            spawn_tool.spawn_agent("researcher", "慢速调研子任务")
            return wait_tool.wait_agents(["researcher"])
        finally:
            set_session(None)
            set_workspace(None)

    pool_store: dict = {}
    with patch("qrclaw.agent.run_sub_agent", side_effect=fake_sub_run), \
         patch("qrclaw.tools.spawn_agent._task_pool", pool_store), \
         patch("qrclaw.sandbox.is_sandbox_enabled", return_value=False), \
         patch("qrclaw.server.routes.run_agent", side_effect=parent_with_spawn), \
         _make_client(workspace) as client, \
         ThreadPoolExecutor(max_workers=2) as executor:
        parent_id = client.post("/v1/sessions").json()["id"]
        fut = executor.submit(
            client.post,
            "/v1/chat/completions",
            json={"session_id": parent_id, "messages": [{"role": "user", "content": "启动后台子任务"}]},
        )
        assert sub_started.wait(timeout=2.0)
        assert has_active_sub_agents(parent_id, workspace.sessions_dir) is True
        assert client.get(f"/v1/sessions/{parent_id}").json()["active_subagents"] is True
        assert client.delete(f"/v1/sessions/{parent_id}").status_code == 409

        cancel_res = client.post(f"/v1/sessions/{parent_id}/cancel")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["cancelled"] is True
        assert fut.result(timeout=2.0).status_code == 200

        assert has_active_sub_agents(parent_id, workspace.sessions_dir) is False
        detail = client.get(f"/v1/sessions/{parent_id}").json()
        assert detail["active_subagents"] is False
        assert detail["run_status"] == "idle"
        assert detail["run_error"] is None
        sub_events = [e for e in detail["events"] if e["kind"] == "subagent"]
        assert [e["meta"]["state"] for e in sub_events] == ["running", "interrupted"]
        assert all(item["status"] == "cancelled" for item in pool_store.values())
        assert client.delete(f"/v1/sessions/{parent_id}").status_code == 200

    # 2. PlanExecutorNode 并行子步骤取消场景
    plan_session = Session(workspace.sessions_dir, resume=False)
    plan_session.add({"role": "user", "content": "并行计划"})
    ctx = init_context_manager(plan_session, workspace, is_sub_agent=False)
    ctx.set_plan("测试并行取消", [PlanStep("1", "步骤一"), PlanStep("2", "步骤二")])

    plan_token = CancelToken()
    step_entered = Event()

    def slow_plan_step(_task, _ws, _agent_id, **_kwargs):
        step_entered.set()
        plan_token.wait(timeout=5.0)
        check_cancelled()
        return "ok", None

    def trigger_plan_cancel():
        assert step_entered.wait(timeout=2.0)
        time.sleep(0.05)
        plan_token.cancel()

    Thread(target=trigger_plan_cancel, daemon=True).start()
    with bind_cancel_token(plan_token):
        with pytest.raises(TaskCancelledError):
            PlanExecutorNode().run(
                session=plan_session,
                console=Console(file=StringIO()),
                workspace=workspace,
                auto_confirm=True,
                run_sub_agent_fn=slow_plan_step,
                react_loop_fn=lambda: "汇总",
            )

    assert has_active_sub_agents(plan_session.session_id, workspace.sessions_dir) is False
    assert ctx.plan_state is None
    saved_plan = Session(workspace.sessions_dir, plan_session.session_id)
    end_states = [e["meta"]["state"] for e in saved_plan.events if e["kind"] == "subagent" and e["meta"]["state"] != "running"]
    assert end_states == ["interrupted", "interrupted"]
