from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from qrclaw.memory.context.session import Session, delete_session, list_sessions
from qrclaw.workspace import Workspace
from qrclaw.config import _MODEL_MAX_TOKENS, COMPRESS_THRESHOLD


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
        from qrclaw.agent import _runner
        from qrclaw.memory.wiki.extraction import ExtractionIndexer
        _runner.react_loop.stop_memory_extractions(self.workspace, session_id)
        indexer = ExtractionIndexer(self.workspace.memory_dir, session_id)
        with indexer.lock:
            deleted = delete_session(session_id, self.workspace.sessions_dir)
            if deleted:
                indexer.discard()
        if deleted:
            for child_id in children:
                if child_id:
                    delete_session(child_id, self.workspace.sessions_dir)
        return deleted

    def rewrite_latest_user_turn(self, session: Session, message_id: str, content: str | None) -> None:
        """重写最后一轮消息，并清理被移除的子 Agent 会话。"""
        from qrclaw.agent import _runner
        integration = _runner.react_loop._get_memory_integration(self.workspace, session)
        removed_ids = set()
        removing = False
        for message in session.messages:
            removing = removing or message.get("uuid") == message_id
            if removing and message.get("uuid"):
                removed_ids.add(message["uuid"])
        # 与后台页面保存共用短锁，避免来源校验与消息删改之间出现空隙。
        with integration.extractor.indexer.lock:
            children = session.rewrite_latest_user_turn(message_id, content)
            integration.extractor.discard_messages(removed_ids)
            integration.extractor.collect(session.messages)
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
            "context_tokens": session.context_tokens,
            "context_limit": _MODEL_MAX_TOKENS,
            "compression_threshold": COMPRESS_THRESHOLD,
            "messages": session.messages,
            "events": session.events,
        }

