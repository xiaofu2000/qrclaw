"""保存子 Agent 与父会话的关联，并提供运行中的临时文字。"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from qrclaw.memory.context.session import Session, SessionEvent, delete_session
from qrclaw.workspace import Workspace


_lock = threading.RLock()
_active: dict[str, dict] = {}


def prepare_sub_agent(parent: Session, workspace: Workspace, agent_id: str, title: str) -> str:
    """在启动线程前创建子会话，并按启动顺序记录父会话卡片。"""
    child_id = f"sub-{agent_id}-{uuid.uuid4().hex[:12]}"
    Session(workspace.sessions_dir, session_id=child_id, resume=False).ensure_saved()
    with _lock:
        _active[child_id] = {"parent_id": parent.session_id, "sessions_dir": workspace.sessions_dir.resolve(),
                             "text": "", "revision": 0}
    try:
        parent.add_event(SessionEvent(
            kind="subagent", title="子 Agent 启动", content=title,
            meta={"child_session_id": child_id, "agent_id": agent_id, "state": "running",
                  "after_message_index": len(parent.messages) - 1},
        ))
    except Exception:
        end_sub_agent(child_id)
        delete_session(child_id, workspace.sessions_dir)
        raise
    return child_id


def append_sub_agent_text(child_id: str, delta: str) -> None:
    """追加尚未保存为完整消息的文字，供 SSE 实时显示。"""
    with _lock:
        current = _active.get(child_id)
        if current is not None:
            current["text"] += delta
            current["revision"] += 1


def clear_sub_agent_text(child_id: str) -> None:
    """完整消息入库后清空临时文字。"""
    with _lock:
        current = _active.get(child_id)
        if current is not None:
            current["text"] = ""
            current["revision"] += 1


def sub_agent_snapshot(child_id: str, sessions_dir: Path | None = None) -> dict | None:
    """返回运行中子任务的文字快照；不存在时返回 None。"""
    with _lock:
        current = _active.get(child_id)
        if current is not None and sessions_dir is not None and current["sessions_dir"] != sessions_dir.resolve():
            return None
        return dict(current) if current is not None else None


def has_active_sub_agents(parent_id: str, sessions_dir: Path | None = None) -> bool:
    """判断父会话是否仍有后台子任务。"""
    with _lock:
        return any(item["parent_id"] == parent_id and
                   (sessions_dir is None or item["sessions_dir"] == sessions_dir.resolve())
                   for item in _active.values())


def end_sub_agent(child_id: str) -> None:
    """结束子任务的临时运行状态。"""
    with _lock:
        _active.pop(child_id, None)


def record_sub_agent_end(sessions_dir: Path, parent_id: str, child_id: str, state: str) -> None:
    """将完成或失败状态写回父会话，刷新后仍能还原卡片。"""
    try:
        parent = Session(sessions_dir, session_id=parent_id)
        parent.add_event(SessionEvent(
            kind="subagent", title="子 Agent 完成" if state == "completed" else "子 Agent 失败",
            meta={"child_session_id": child_id, "state": state},
        ))
    finally:
        end_sub_agent(child_id)
