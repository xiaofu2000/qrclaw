"""
Session 会话管理模块测试

测试覆盖：
- 会话创建与恢复
- 消息添加与持久化
- Token 计算
- 计划管理
"""
import pytest
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from qrclaw.memory.context.session import (
    Session,
    list_sessions,
    get_last_session_id,
    delete_session,
    count_tokens,
)


class TestSessionCreation:
    """测试会话创建"""

    def test_create_new_session(self, temp_dir):
        """测试创建新会话"""
        session = Session(temp_dir, resume=False)

        assert session.session_id is not None
        assert len(session.session_id) > 0
        assert session.messages == []
        assert session.prompt_tokens == 0

    def test_create_session_with_id(self, temp_dir):
        """测试指定 ID 创建会话"""
        session = Session(temp_dir, session_id="test-session-123")

        assert session.session_id == "test-session-123"
        assert session._path.name == "test-session-123.json"

    def test_session_id_cannot_escape_directory(self, temp_dir):
        sessions_dir = temp_dir / "sessions"
        outside = temp_dir / "outside.json"
        with pytest.raises(ValueError, match="无效的会话 ID"):
            Session(sessions_dir, session_id="../outside")
        with pytest.raises(ValueError, match="无效的会话 ID"):
            delete_session("../outside", sessions_dir)
        assert not outside.exists()

    def test_corrupt_session_does_not_block_startup(self, temp_dir):
        path = temp_dir / "broken.json"
        path.write_text("{", encoding="utf-8")

        session = Session(temp_dir, session_id="broken")

        assert session.messages == []
        assert path.read_text(encoding="utf-8") == "{"

    def test_resume_last_session(self, temp_dir):
        """测试恢复最近会话"""
        # 创建第一个会话
        session1 = Session(temp_dir, resume=False)
        session1.add({"role": "user", "content": "hello"})

        # 创建第二个会话（应该恢复第一个）
        session2 = Session(temp_dir, resume=True)

        # 应该是同一个会话
        assert session2.session_id == session1.session_id
        assert len(session2.messages) == 1
        assert session2.messages[0]["content"] == "hello"

    def test_no_resume_creates_new(self, temp_dir):
        """测试不恢复时创建新会话"""
        # 创建第一个会话
        session1 = Session(temp_dir, resume=False)
        session1.add({"role": "user", "content": "test"})

        # 不恢复，创建新会话
        session2 = Session(temp_dir, resume=False)

        # 应该是不同的会话
        assert session2.session_id != session1.session_id
        assert len(session2.messages) == 0


class TestSessionMessages:
    """测试消息管理"""

    def test_add_message(self, temp_dir):
        """测试添加消息"""
        session = Session(temp_dir, resume=False)

        session.add({"role": "user", "content": "你好"})
        session.add({"role": "assistant", "content": "你好！有什么可以帮你的？"})

        assert len(session.messages) == 2
        assert session.messages[0]["role"] == "user"
        assert session.messages[0]["content"] == "你好"

        # 验证持久化
        saved_data = json.loads(session._path.read_text(encoding="utf-8"))
        assert len(saved_data["messages"]) == 2

    def test_message_persistence(self, temp_dir):
        """测试消息持久化"""
        # 创建会话并添加消息
        session_id = "persist-test"
        session1 = Session(temp_dir, session_id=session_id)
        session1.add({"role": "user", "content": "测试持久化"})
        session1.add({"role": "assistant", "content": "收到"})

        # 重新加载会话
        session2 = Session(temp_dir, session_id=session_id)

        # 验证消息已加载
        assert len(session2.messages) == 2
        assert session2.messages[0]["content"] == "测试持久化"

    def test_two_writers_keep_both_messages(self, temp_dir):
        first = Session(temp_dir, session_id="shared")
        second = Session(temp_dir, session_id="shared")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda item: item[0].add({"role": "user", "content": item[1]}), [(first, "one"), (second, "two")]))
        saved = Session(temp_dir, session_id="shared")
        assert {message["content"] for message in saved.messages} == {"one", "two"}

    def test_clear_session(self, temp_dir):
        """测试清空会话"""
        session = Session(temp_dir, resume=False)
        session.add({"role": "user", "content": "test"})

        # 清空
        session.clear()

        assert session.messages == []
        assert not session._path.exists()

    def test_rewrite_latest_user_turn_keeps_earlier_history(self, temp_dir):
        """重写末轮时保留先前消息和过程，并返回末轮子会话。"""
        session = Session(temp_dir, session_id="rewrite")
        session.add({"role": "user", "content": "第一轮"})
        session.add_event({"kind": "status", "title": "执行中", "content": "第一轮过程"})
        session.add({"role": "assistant", "content": "第一轮回复"})
        session.add({"role": "user", "content": "第二轮"})
        last_id = session.messages[-1]["uuid"]
        session.add_event({"kind": "subagent", "meta": {"child_session_id": "sub-test"}})
        session.add({"role": "assistant", "content": "第二轮回复"})

        assert session.rewrite_latest_user_turn(last_id, "修改后") == {"sub-test"}
        restored = Session(temp_dir, session_id="rewrite")
        assert [m["content"] for m in restored.messages] == ["第一轮", "第一轮回复", "修改后"]
        assert restored.messages[-1]["uuid"] == last_id
        assert [e["content"] for e in restored.events] == ["第一轮过程"]
        assert restored.history_version == 1
        assert restored.rewrite_latest_user_turn(last_id, None) == set()
        assert [m["content"] for m in Session(temp_dir, session_id="rewrite").messages] == ["第一轮", "第一轮回复"]

    def test_rewrite_rejects_stale_message_id(self, temp_dir):
        """过期消息 ID 不能覆盖当前轮次。"""
        session = Session(temp_dir, session_id="rewrite-stale")
        session.add({"role": "user", "content": "旧"})
        old_id = session.messages[-1]["uuid"]
        session.add({"role": "user", "content": "新"})
        with pytest.raises(ValueError, match="最后一条"):
            session.rewrite_latest_user_turn(old_id, "覆盖")
        assert [m["content"] for m in Session(temp_dir, session_id="rewrite-stale").messages] == ["旧", "新"]

    def test_rewrite_legacy_turn_keeps_earlier_process_when_markers_exist(self, temp_dir):
        """旧会话缺少轮次索引时，尽量按消息保存事件保留先前过程。"""
        session = Session(temp_dir, session_id="legacy")
        session.add({"role": "user", "content": "第一轮"})
        session.add_event({"kind": "status", "title": "执行中", "content": "保留"})
        session.add({"role": "assistant", "content": "第一轮回复"})
        session.add_event({"kind": "message_saved", "title": "消息已保存"})
        session.add({"role": "user", "content": "第二轮"})
        last_id = session.messages[-1]["uuid"]
        session.add_event({"kind": "status", "title": "执行中", "content": "删除"})
        raw = json.loads(session._path.read_text(encoding="utf-8"))
        raw["messages"][-1].pop("turn_event_index")
        session._path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        session.rewrite_latest_user_turn(last_id, None)
        assert [event["kind"] for event in Session(temp_dir, session_id="legacy").events] == ["status", "message_saved"]

    @pytest.mark.parametrize("content", [None, "修改后"])
    def test_rewrite_legacy_turn_with_partial_save_events_clears_process(self, temp_dir, content):
        """部分旧消息没有保存事件时，不能把末轮事件误认成早期记录。"""
        session = Session(temp_dir, session_id="legacy-partial")
        session.add({"role": "user", "content": "第一轮"})
        session.add({"role": "assistant", "content": "旧回复"})
        session.add({"role": "user", "content": "第二轮"})
        last_id = session.messages[-1]["uuid"]
        session.add_event({"kind": "subagent", "meta": {"child_session_id": "sub-last"}})
        session.add({"role": "assistant", "content": "末轮回复"})
        session.add_event({"kind": "message_saved", "title": "消息已保存"})
        raw = json.loads(session._path.read_text(encoding="utf-8"))
        for message in raw["messages"]:
            message.pop("turn_event_index", None)
        session._path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        assert session.rewrite_latest_user_turn(last_id, content) == {"sub-last"}
        restored = Session(temp_dir, session_id="legacy-partial")
        expected = ["第一轮", "旧回复"] + ([content] if content is not None else [])
        assert [message["content"] for message in restored.messages] == expected
        assert restored.events == []
        assert restored.history_version == 1


class TestTokenCounting:
    """测试 Token 计算"""

    def test_count_tokens_basic(self):
        """测试基础 token 计算"""
        messages = [
            {"role": "user", "content": "hello"}
        ]
        tokens = count_tokens(messages)

        # 至少包含消息内容 token
        assert tokens > 0

    def test_count_tokens_multiple_messages(self):
        """测试多条消息的 token 计算"""
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"}
        ]
        tokens = count_tokens(messages)

        # 应该比单条消息多
        single_tokens = count_tokens([messages[0]])
        assert tokens > single_tokens

    def test_count_tokens_empty_messages(self):
        """测试空消息列表"""
        tokens = count_tokens([])
        # 空消息的开销由底层 token 计数器决定
        assert tokens >= 0

    def test_llm_usage_survives_message_save_and_reload(self, temp_dir):
        session = Session(temp_dir, session_id="usage")
        session.start_run_usage()
        session.update_tokens(10, 2, 12)
        session.update_tokens(15, 3, 18)
        session.add({"role": "assistant", "content": "完成"})

        assert session.run_usage == {"prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30}
        restored = Session(temp_dir, session_id="usage")
        assert restored.usage == session.run_usage
        assert restored.prompt_tokens == count_tokens(restored.messages)


class TestSessionList:
    """测试会话列表管理"""

    def test_latest_reply_id_excludes_tools_reports_and_prior_turns(self, temp_dir):
        """只为末轮正文提供稳定标识，改名不让已读回复重新出现。"""
        from qrclaw.memory.context.session.session import latest_reply_id
        session = Session(temp_dir, session_id="unread")
        session.add({"role": "user", "content": "问题"})
        session.add({"role": "assistant", "content": "调用工具", "tool_calls": [
            {"id": "call", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        session.add({"role": "assistant", "kind": "execution_report", "content": "报告"})
        assert list_sessions(temp_dir)[0]["last_reply_id"] is None
        session.add({"role": "assistant", "content": "正文回复"})
        reply_id = session.messages[-1]["uuid"]
        session.set_title("新标题")
        assert list_sessions(temp_dir)[0]["last_reply_id"] == reply_id
        session.add({"role": "user", "content": "下一轮"})
        assert list_sessions(temp_dir)[0]["last_reply_id"] is None
        legacy = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧答复"}]
        assert latest_reply_id(legacy, 2) == "legacy:2:1"
        assert latest_reply_id([{"role": "assistant", "content": "历史摘要"}]) is None

    def test_list_sessions(self, temp_dir):
        """测试列出会话"""
        # 创建多个会话
        Session(temp_dir, session_id="session-1").add({"role": "user", "content": "test1"})
        Session(temp_dir, session_id="session-2").add({"role": "user", "content": "test2"})

        sessions = list_sessions(temp_dir)

        assert len(sessions) == 2
        assert any(s["id"] == "session-1" for s in sessions)
        assert any(s["id"] == "session-2" for s in sessions)

    def test_get_last_session_id(self, temp_dir):
        """测试获取最近会话 ID"""
        # 创建两个会话
        Session(temp_dir, session_id="older").add({"role": "user", "content": "test1"})

        # 等一下，确保时间戳不同
        import time
        time.sleep(0.01)

        Session(temp_dir, session_id="newer").add({"role": "user", "content": "test2"})

        # 应该返回最新的
        last_id = get_last_session_id(temp_dir)
        assert last_id == "newer"

    def test_delete_session(self, temp_dir):
        """测试删除会话"""
        session = Session(temp_dir, session_id="to-delete")
        session.add({"role": "user", "content": "test"})

        # 删除
        result = delete_session("to-delete", temp_dir)

        assert result is True
        assert not session._path.exists()

    def test_delete_nonexistent_session(self, temp_dir):
        """测试删除不存在的会话"""
        result = delete_session("nonexistent", temp_dir)
        assert result is False


def test_cached_message_tokens_match_full_count_and_follow_content_changes():
    """按消息缓存的 token 数与整体计算一致，内容变化后重新计算。"""
    import litellm
    from qrclaw.config import LITELLM_MODEL
    from qrclaw.memory.token_utils import count_messages_tokens

    messages = [
        {"role": "user", "content": "帮我看看这个函数"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                             "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "def f():\n    return 1\n" * 20},
        {"role": "assistant", "content": "这个函数返回 1。"},
    ]
    assert count_messages_tokens(messages) == litellm.token_counter(model=LITELLM_MODEL, messages=messages)
    changed = [*messages[:-1], {"role": "assistant", "content": "这个函数总是返回整数 1，没有参数。"}]
    assert count_messages_tokens(changed) == litellm.token_counter(model=LITELLM_MODEL, messages=changed)
    assert count_messages_tokens([]) == litellm.token_counter(model=LITELLM_MODEL, messages=[])


def test_load_skips_parsing_until_file_changes(temp_dir, monkeypatch):
    """文件未变时追加不重新解析整份会话；其他对象写入后能读到对方的改动。"""
    import qrclaw.memory.context.session.session as session_module

    first = Session(temp_dir, session_id="shared", resume=False)
    first.add({"role": "user", "content": "第一条"})
    parses = []
    real_loads = session_module.json.loads
    monkeypatch.setattr(session_module.json, "loads", lambda text: parses.append(1) or real_loads(text))

    first.add_event({"kind": "status", "title": "执行中"})
    first.add({"role": "assistant", "content": "回复"})
    assert parses == []

    second = Session(temp_dir, session_id="shared")
    second.add({"role": "user", "content": "另一个对象写入"})
    first.add({"role": "assistant", "content": "再回复"})
    assert [m["content"] for m in Session(temp_dir, session_id="shared").messages] == ["第一条", "回复", "另一个对象写入", "再回复"]
    assert len(first.events) == 1


def test_failed_save_forces_reload(temp_dir, monkeypatch):
    """保存失败后内存里未落盘的消息不会在下次写入时被带上。"""
    session = Session(temp_dir, session_id="fail", resume=False)
    session.add({"role": "user", "content": "已保存"})
    real_replace = Path.replace

    def broken_replace(self, target):
        raise OSError("磁盘已满")

    monkeypatch.setattr(Path, "replace", broken_replace)
    with pytest.raises(OSError):
        session.add({"role": "assistant", "content": "未保存"})
    monkeypatch.setattr(Path, "replace", real_replace)
    session.add({"role": "assistant", "content": "重新保存"})
    assert [m["content"] for m in Session(temp_dir, session_id="fail").messages] == ["已保存", "重新保存"]


def test_failed_mutation_before_save_does_not_persist_partial_state(temp_dir, monkeypatch):
    """改写历史在保存前抛错（如分词失败）时，下次写入从文件重读，不会把截断后的内存状态存进去。"""
    import qrclaw.memory.context.session.session as session_module

    session = Session(temp_dir, session_id="partial", resume=False)
    session.add({"role": "user", "content": "q1"})
    session.add({"role": "assistant", "content": "a1"})
    real_count = session_module.count_messages_tokens

    def broken(_messages):
        raise RuntimeError("分词失败")

    monkeypatch.setattr(session_module, "count_messages_tokens", broken)
    with pytest.raises(RuntimeError):
        session.rewrite_latest_user_turn(session.messages[0]["uuid"], None)
    monkeypatch.setattr(session_module, "count_messages_tokens", real_count)
    session.add({"role": "user", "content": "q2"})
    assert [m["content"] for m in Session(temp_dir, session_id="partial").messages] == ["q1", "a1", "q2"]
