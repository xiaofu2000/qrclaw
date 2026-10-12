from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from qrclaw.memory.context.session import Session, delete_session, list_sessions
from qrclaw.workspace import Workspace


class SessionService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def list_sessions(self) -> list[dict[str, Any]]:
        return list_sessions(self.workspace.sessions_dir)

    def get_session(self, session_id: str) -> Session:
        return Session(sessions_dir=self.workspace.sessions_dir, session_id=session_id)

    def create_session(self, session_id: str | None = None) -> Session:
        session = Session(
            sessions_dir=self.workspace.sessions_dir,
            session_id=session_id or None,
            resume=False,
        )
        session.ensure_saved()
        return session

    def delete_session(self, session_id: str) -> bool:
        """删除父会话时一并清理其隐藏的子任务会话。"""
        parent = self.get_session(session_id)
        children = {
            (event.get("meta") or {}).get("child_session_id")
            for event in parent.events if event.get("kind") == "subagent"
        }
        deleted = delete_session(session_id, self.workspace.sessions_dir)
        if deleted:
            for child_id in children:
                if child_id:
                    delete_session(child_id, self.workspace.sessions_dir)
        return deleted

    def rewrite_latest_user_turn(self, session: Session, message_id: str, content: str | None) -> None:
        """重写最后一轮消息，并清理被移除的子 Agent 会话。"""
        children = session.rewrite_latest_user_turn(message_id, content)
        for child_id in children:
            delete_session(child_id, self.workspace.sessions_dir)

    @staticmethod
    def describe_session(session: Session) -> dict[str, Any]:
        path = session._path
        updated_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if path.exists() else ""
        return {
            "id": session.session_id,
            "title": session.title or next((" ".join(message["content"].split())[:48]
                for message in session.messages if message.get("role") == "user"
                and isinstance(message.get("content"), str)), "新对话"),
            "history_version": session.history_version,
            "message_count": len(session.messages),
            "updated_at": updated_at,
            "prompt_tokens": session.usage["prompt_tokens"],
            "completion_tokens": session.usage["completion_tokens"],
            "total_tokens": session.usage["total_tokens"],
            "messages": session.messages,
            "events": session.events,
        }

