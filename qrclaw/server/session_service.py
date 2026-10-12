from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any
from filelock import FileLock

from qrclaw.memory.context.session import Session, delete_session, list_sessions
from qrclaw.workspace import Workspace
from qrclaw.config import _MODEL_MAX_TOKENS, COMPRESS_THRESHOLD


class SessionService:
    """管理会话及绑定的记忆来源，删改中断后继续完成已持久化的操作。"""

    def __init__(self, workspace: Workspace):
        """会话和 Wiki 使用同一工作空间。"""
        self.workspace = workspace

    def list_sessions(self) -> list[dict[str, Any]]:
        return list_sessions(self.workspace.sessions_dir)

    def get_session(self, session_id: str) -> Session:
        self.recover_session_changes(session_id)
        return Session(sessions_dir=self.workspace.sessions_dir, session_id=session_id)

    def create_session(self, session_id: str | None = None) -> Session:
        if session_id is not None:
            self.recover_session_changes(session_id)
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
        from qrclaw.agent import _runner
        from qrclaw.memory.wiki.extraction import ExtractionIndexer
        _runner.react_loop.stop_memory_extractions(self.workspace, session_id)
        indexer = ExtractionIndexer(self.workspace.memory_dir, session_id)
        with indexer.lock:
            def before_delete():
                """撤销和删除意图落盘失败时，保留原聊天文件。"""
                parent._load(strict=True)
                children = {child_id for event in parent.events if event.get("kind") == "subagent"
                            if (child_id := (event.get("meta") or {}).get("child_session_id"))}
                self._record_change(parent, None, None, children)
            deleted = delete_session(session_id, self.workspace.sessions_dir, before_delete=before_delete)
        if deleted:
            self.recover_session_changes(session_id)
        return deleted

    def rewrite_latest_user_turn(self, session: Session, message_id: str, content: str | None) -> None:
        """重写最后一轮消息，并清理被移除的子 Agent 会话。"""
        from qrclaw.agent import _runner
        self.recover_session_changes(session.session_id)
        integration = _runner.react_loop._get_memory_integration(self.workspace, session)
        # 与后台页面保存共用短锁，避免来源校验与消息删改之间出现空隙。
        with integration.extractor.indexer.lock:
            session.rewrite_latest_user_turn(message_id, content,
                before_save=lambda text, removed, children: self._record_change(session, text, removed, children))
        self.recover_session_changes(session.session_id)

    def _record_change(self, session: Session, replacement: str | None, message_ids: set[str] | None, children: set[str]) -> None:
        """调用方持会话文件锁；来源撤销与待完成操作在同一状态文件中原子落盘。"""
        from qrclaw.memory.wiki import WikiMemory
        change = {"expected_hash": hashlib.sha256(session._path.read_bytes()).hexdigest(),
                  "replacement": replacement, "message_ids": sorted(message_ids) if message_ids is not None else None,
                  "children": sorted(children)}
        WikiMemory.for_workspace(self.workspace.memory_dir).provenance.invalidate(
            session.session_id, message_ids, session_change=change)

    def recover_session_changes(self, session_id: str | None = None) -> None:
        """续接删改及来源进度；文件已另行变化时明确报错，禁止覆盖新聊天。"""
        from qrclaw.agent import _runner
        from qrclaw.memory.wiki import WikiMemory
        from qrclaw.memory.wiki.extraction import ExtractionIndexer
        from qrclaw.memory.wiki.index import atomic_write
        wiki = WikiMemory.for_workspace(self.workspace.memory_dir)
        with wiki._lock:
            pending = wiki.provenance.read()["session_changes"]
        for current_id, change in pending.items():
            if session_id is not None and current_id != session_id:
                continue
            session = Session(self.workspace.sessions_dir, current_id)
            replacement = change["replacement"]
            integration = _runner.react_loop._get_memory_integration(self.workspace, session) if replacement is not None else None
            indexer = integration.extractor.indexer if integration is not None else ExtractionIndexer(self.workspace.memory_dir, current_id)
            with indexer.lock:
                with wiki._lock:
                    if wiki.provenance.read()["session_changes"].get(current_id) != change:
                        continue
                with FileLock(f"{session._path}.lock"):
                    current_hash = hashlib.sha256(session._path.read_bytes()).hexdigest() if session._path.exists() else None
                    replacement_hash = hashlib.sha256(replacement.encode()).hexdigest() if replacement is not None else None
                    if current_hash == change["expected_hash"]:
                        if replacement is None:
                            session._path.unlink()
                        else:
                            atomic_write(session._path, replacement)
                    elif current_hash != replacement_hash:
                        raise RuntimeError("会话在未完成的删改期间发生变化，已保留文件，请检查来源恢复记录")
                for child_id in change["children"]:
                    delete_session(child_id, self.workspace.sessions_dir)
                if replacement is None:
                    indexer.discard()
                else:
                    session._load(strict=True)
                    integration.extractor.discard_messages(set(change["message_ids"]))
                with wiki._lock:
                    state = wiki.provenance.read()
                    state["session_changes"].pop(current_id, None)
                    wiki.provenance.write(state)
                    if replacement is not None:
                        integration.extractor.collect(session.messages)
            wiki.repair_worker.resume()

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

