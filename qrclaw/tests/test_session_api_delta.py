"""会话接口的增量读取、状态代码、工具调用记录与自动标题。"""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from qrclaw.memory.context.session import Session, SessionEvent, status_event
from qrclaw.memory.context.session.events import STATUS_TITLES, status_code
from qrclaw.server.app import create_app
from qrclaw.server.session_titles import clean_title, generate_title, suggest_title
from qrclaw.tools.call_context import collect_tool_detail, current_call_id, report_tool_detail, tool_call
from qrclaw.workspace import Workspace


def _client(workspace):
    return TestClient(create_app(workspace), client=("127.0.0.1", 50000))


def test_detail_returns_only_new_messages_and_events_for_same_history(temp_dir):
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="delta", resume=False)
    for index in range(3):
        session.add({"role": "user" if index % 2 == 0 else "assistant", "content": f"第 {index} 条"})
        session.add_event(SessionEvent(kind="message_saved", title="消息已保存"))
    with _client(workspace) as client:
        full = client.get("/v1/sessions/delta").json()
        session.add({"role": "assistant", "content": "新回复"})
        session.add_event(SessionEvent(kind="message_saved", title="消息已保存"))
        delta = client.get("/v1/sessions/delta", params={"messages_from": 3, "events_from": 3, "history_version": 0}).json()
    assert (full["messages_offset"], full["events_offset"], len(full["messages"])) == (0, 0, 3)
    assert (delta["messages_offset"], delta["events_offset"]) == (3, 3)
    assert [message["content"] for message in delta["messages"]] == ["新回复"]
    assert len(delta["events"]) == 1
    assert (delta["message_count"], delta["event_count"]) == (4, 4)


def test_detail_falls_back_to_full_history_when_version_or_offset_is_stale(temp_dir):
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="stale", resume=False)
    session.add({"role": "user", "content": "问题"})
    session.add({"role": "assistant", "content": "回复"})
    with _client(workspace) as client:
        other_version = client.get("/v1/sessions/stale", params={"messages_from": 1, "history_version": 5}).json()
        too_far = client.get("/v1/sessions/stale", params={"messages_from": 9, "history_version": 0}).json()
        no_version = client.get("/v1/sessions/stale", params={"messages_from": 1}).json()
    for detail in (other_version, too_far, no_version):
        assert detail["messages_offset"] == 0
        assert [message["content"] for message in detail["messages"]] == ["问题", "回复"]


def test_status_events_carry_codes_and_old_titles_are_backfilled(temp_dir):
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="codes", resume=False)
    session.add_event(status_event("step_running", "检查代码"))
    session.add_event(SessionEvent(kind="status", title="重规划中", content="旧会话保存的事件"))
    with _client(workspace) as client:
        events = client.get("/v1/sessions/codes").json()["events"]
    assert [event["meta"]["code"] for event in events] == ["step_running", "replanning"]
    assert events[0]["title"] == STATUS_TITLES["step_running"]
    assert status_code({"kind": "tool_status", "title": "执行中"}) is None


def test_progress_stream_uses_codes_for_step_events(temp_dir):
    """改了展示标题的步骤事件仍按代码推送，规划失败原因仍不推送。"""
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="stream-codes", resume=False)
    session.add_event(SessionEvent(kind="status", title="正在处理第一步", content="新文案步骤", meta={"code": "step_running"}))
    session.add_event(status_event("plan_failed", "内部错误详情"))
    with _client(workspace) as client:
        text = client.get("/v1/sessions/stream-codes/events/stream").text
    assert "新文案步骤" in text
    assert "内部错误详情" not in text


def test_tool_context_exposes_call_id_and_collects_reported_detail():
    assert current_call_id() is None
    report_tool_detail(pages=["忽略"])  # 不在收集期间时安全忽略
    with tool_call("call-1"), collect_tool_detail() as detail:
        assert current_call_id() == "call-1"
        report_tool_detail(pages=["QRClaw Skill 机制"])
    assert detail == {"pages": ["QRClaw Skill 机制"]}
    assert current_call_id() is None


def test_tool_events_record_call_id_and_written_pages(temp_dir, monkeypatch):
    """react 循环把调用 ID 与工具上报的页面名写进完成事件，界面据此对应工具结果消息。"""
    from qrclaw.graph.nodes import react_loop

    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="tools", resume=False)
    session.add({"role": "user", "content": "记住这个"})

    class FakeRunner:
        last_error = False

        def __init__(self, **_kwargs):
            pass

        def run(self, name, arguments):
            report_tool_detail(pages=["代码分析工具偏好"])
            return "已写入 1 个 Wiki 页面：代码分析工具偏好。"

    replies = iter([
        SimpleNamespace(content="", tool_calls=[SimpleNamespace(id="call-9", name="write_wiki_page", arguments='{"content": "x"}',
                                                                thought_signature=None)],
                        finish_reason="tool_calls", reasoning_content=None, prompt_tokens=0, completion_tokens=0, total_tokens=0),
        SimpleNamespace(content="好的", tool_calls=[], finish_reason="stop", reasoning_content=None,
                        prompt_tokens=0, completion_tokens=0, total_tokens=0),
    ])
    llm = SimpleNamespace(chat=lambda *_args, **_kwargs: next(replies))
    monkeypatch.setattr(react_loop, "ToolRunner", FakeRunner)
    monkeypatch.setattr(react_loop, "get_llm_service", lambda: llm)

    class FakeContext:
        def compress_if_needed(self):
            pass

        def build_messages(self, _role):
            return [{"role": "user", "content": "记住这个"}]

    monkeypatch.setattr(react_loop, "get_context_manager", lambda: FakeContext())
    node = react_loop.ReactLoopNode()
    monkeypatch.setattr(node, "_get_memory_integration", lambda *_args: None)
    from rich.console import Console
    from io import StringIO
    node.run(session, Console(file=StringIO()), workspace, is_sub_agent=True)

    saved = Session(workspace.sessions_dir, session_id="tools")
    tool_events = [event for event in saved.events if event["kind"] == "tool_status"]
    assert [event["meta"]["state"] for event in tool_events] == ["started", "completed"]
    assert all(event["meta"]["call_id"] == "call-9" for event in tool_events)
    assert tool_events[-1]["meta"]["pages"] == ["代码分析工具偏好"]
    assert "pages" not in tool_events[0]["meta"]
    assert next(message for message in saved.messages if message["role"] == "tool")["tool_call_id"] == "call-9"


def test_title_is_generated_once_and_never_overwrites_user_rename(temp_dir):
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, session_id="title", resume=False)
    session.add({"role": "user", "content": "npx skills add morluto/rea 现在的 qrclaw 没适配吧"})
    session.add({"role": "assistant", "content": "对，没适配，只能手动拷贝 SKILL.md。"})
    prompts = []

    def chat(messages, **_kwargs):
        prompts.append(messages)
        return SimpleNamespace(content="标题：「QRClaw 能否安装 REA skill」。\n解释略")

    llm = SimpleNamespace(chat=chat)
    assert generate_title(workspace.sessions_dir, "title", llm) == "QRClaw 能否安装 REA skill"
    assert Session(workspace.sessions_dir, "title").title == "QRClaw 能否安装 REA skill"
    assert generate_title(workspace.sessions_dir, "title", llm) is None
    assert len(prompts) == 1

    renamed = Session(workspace.sessions_dir, session_id="renamed", resume=False)
    renamed.add({"role": "user", "content": "问题"})
    renamed.add({"role": "assistant", "content": "回答"})
    renamed.set_title("我起的名字")
    assert generate_title(workspace.sessions_dir, "renamed", llm) is None
    assert renamed.set_title("自动标题", only_if_unset=True) is False
    assert Session(workspace.sessions_dir, "renamed").title == "我起的名字"


def test_title_needs_a_reply_and_survives_model_failure():
    def broken(*_args, **_kwargs):
        raise RuntimeError("offline")

    assert suggest_title([{"role": "user", "content": "只有问题"}], SimpleNamespace(chat=broken)) is None
    assert suggest_title([{"role": "user", "content": "问"}, {"role": "assistant", "content": "答"}],
                         SimpleNamespace(chat=broken)) is None
    assert clean_title("《整理记忆系统》") == "整理记忆系统"
    assert len(clean_title("很" * 80)) == 24
