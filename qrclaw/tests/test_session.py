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
