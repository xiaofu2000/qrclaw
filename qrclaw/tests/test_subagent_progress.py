"""父子 Agent 会话关联、实时过程和失败恢复。"""

from threading import Event, Thread
from time import sleep
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qrclaw.agent import run_sub_agent, set_session, set_workspace
from qrclaw.memory.context.session import Session, SessionEvent
from qrclaw.server.app import create_app
from qrclaw.subagent_progress import (
    append_sub_agent_text, has_active_sub_agents, prepare_sub_agent, record_sub_agent_end, sub_agent_snapshot,
)
from qrclaw.workspace import Workspace
from qrclaw.tools import spawn_agent as spawn_tool, wait_agents as wait_tool


def test_child_card_links_to_persisted_session_and_finishes(temp_dir):
    """卡片先于执行出现，子任务完成后仍能从会话文件还原。"""
    workspace = Workspace(_root=temp_dir)
    parent = Session(workspace.sessions_dir, resume=False)
    parent.add({"role": "user", "content": "做三件事"})
    child_id = prepare_sub_agent(parent, workspace, "step-1", "检查代码")
    other_workspace = Workspace(_root=temp_dir / "other")
    assert has_active_sub_agents(parent.session_id, workspace.sessions_dir)
    assert not has_active_sub_agents(parent.session_id, other_workspace.sessions_dir)
    assert parent.events[-1]["meta"]["after_message_index"] == 0
    assert parent.events[-1]["meta"]["child_session_id"] == child_id
    assert Session(workspace.sessions_dir, child_id).messages == []

    def fake_run(_task, child, _console, _workspace, **kwargs):
        kwargs["on_delta"]("正在检查")
        assert sub_agent_snapshot(child_id)["text"] == "正在检查"
        child.add({"role": "assistant", "content": "检查完成"})
        return "检查完成"

    with patch("qrclaw.sandbox.is_sandbox_enabled", return_value=False), \
         patch("qrclaw.sandbox.sandbox_manager.has_sandbox", return_value=False), \
         patch("qrclaw.agent.run", side_effect=fake_run):
        result, child = run_sub_agent("检查", workspace, "step-1", child_session_id=child_id,
                                      parent_session_id=parent.session_id)

    assert result == "检查完成"
    assert child.session_id == child_id
    assert Session(workspace.sessions_dir, child_id).messages[-1]["content"] == "检查完成"
    assert Session(workspace.sessions_dir, parent.session_id).events[-1]["meta"]["state"] == "completed"
    assert sub_agent_snapshot(child_id) is None


def test_child_failure_updates_only_its_own_card(temp_dir):
    """同名子任务具有不同 ID，失败不会覆盖前一个卡片。"""
    workspace = Workspace(_root=temp_dir)
    parent = Session(workspace.sessions_dir, resume=False)
    first = prepare_sub_agent(parent, workspace, "worker", "任务一")
    second = prepare_sub_agent(parent, workspace, "worker", "任务二")
    assert first != second
    record_sub_agent_end(workspace.sessions_dir, parent.session_id, first, "completed")

    with patch("qrclaw.sandbox.is_sandbox_enabled", return_value=False), \
         patch("qrclaw.sandbox.sandbox_manager.has_sandbox", return_value=False), \
         patch("qrclaw.agent.run", side_effect=RuntimeError("子任务失败")):
        with pytest.raises(RuntimeError, match="子任务失败"):
            run_sub_agent("失败任务", workspace, "worker", child_session_id=second,
                          parent_session_id=parent.session_id)

    events = Session(workspace.sessions_dir, parent.session_id).events
    assert [(event["meta"]["child_session_id"], event["meta"]["state"]) for event in events] == [
        (first, "running"), (second, "running"), (first, "completed"), (second, "failed"),
    ]
    assert Session(workspace.sessions_dir, second).events[-1]["content"] == "子任务失败"
    assert sub_agent_snapshot(second) is None


def test_child_progress_stream_and_parent_card_survive_refresh(temp_dir):
    """子任务文字走实时流，工具状态与卡片留在会话事件中。"""
    workspace = Workspace(_root=temp_dir)
    parent = Session(workspace.sessions_dir, resume=False)
    parent.add({"role": "user", "content": "检查"})
    child_id = prepare_sub_agent(parent, workspace, "worker", "查文件")
    child = Session(workspace.sessions_dir, child_id)
    child.add_event(SessionEvent(kind="tool_status", title="工具开始", content="read_file"))
    child.add({"role": "tool", "content": "文件正文"})
    append_sub_agent_text(child_id, "读取中")

    def finish():
        sleep(0.3)
        record_sub_agent_end(workspace.sessions_dir, parent.session_id, child_id, "completed")

    worker = Thread(target=finish)
    worker.start()
    with TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        assert client.get(f"/v1/sessions/{parent.session_id}").json()["active_subagents"] is True
        assert client.delete(f"/v1/sessions/{parent.session_id}").status_code == 409
        live = client.get(f"/v1/sessions/{child_id}/events/stream")
        saved = client.get(f"/v1/sessions/{child_id}").json()
        cards = client.get(f"/v1/sessions/{parent.session_id}/events/stream").text
        assert client.get("/v1/sessions").json()[0]["id"] == parent.session_id
        assert client.delete(f"/v1/sessions/{parent.session_id}").status_code == 200
        assert client.get(f"/v1/sessions/{child_id}/events/stream").status_code == 404
    worker.join()

    assert 'event: live_text' in live.text and "读取中" in live.text
    assert '"kind": "tool_status"' in live.text
    assert "文件正文" not in live.text
    assert saved["messages"][-1]["content"] == "文件正文"
    assert child_id in cards and '"state": "completed"' in cards


def test_direct_spawn_and_wait_are_scoped_to_parent_session(temp_dir):
    """两个父会话可运行同名子 Agent，等待结果只收集当前会话。"""
    workspace = Workspace(_root=temp_dir)
    first = Session(workspace.sessions_dir, resume=False)
    second = Session(workspace.sessions_dir, resume=False)
    first_gate, second_gate = Event(), Event()

    def fake_run(_task, _workspace, _agent_id, **kwargs):
        gate = first_gate if kwargs["parent_session_id"] == first.session_id else second_gate
        assert gate.wait(3)
        return "完成", None

    try:
        with patch("qrclaw.agent.run_sub_agent", side_effect=fake_run), \
             patch("qrclaw.tools.spawn_agent._task_pool", {}), \
             patch("qrclaw.sandbox.is_sandbox_enabled", return_value=False):
            set_session(first)
            set_workspace(workspace)
            assert "已在后台启动" in spawn_tool.spawn_agent("worker", "任务一")
            set_session(second)
            assert "已在后台启动" in spawn_tool.spawn_agent("worker", "任务二")
            assert len([e for e in Session(workspace.sessions_dir, first.session_id).events if e["kind"] == "subagent"]) == 1
            second_gate.set()
            assert "完成" in wait_tool.wait_agents(["worker"])
            assert sub_agent_snapshot(Session(workspace.sessions_dir, first.session_id).events[0]["meta"]["child_session_id"])
            set_session(first)
            first_gate.set()
            assert "完成" in wait_tool.wait_agents(["worker"])
    finally:
        first_gate.set()
        second_gate.set()
        set_session(None)
        set_workspace(None)
