"""使用 SQLite 持久化会话元数据、运行快照与事件。"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qrclaw.execution.models import EventEnvelope, RunSnapshot


def _now_text() -> str:
    """返回数据库使用的 UTC 时间文本。"""

    return datetime.now(timezone.utc).isoformat()


class RuntimeStore:
    """运行服务的本地 SQLite 存储。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """在事务结束后关闭连接；SQLite 自带的上下文只负责提交和回滚。"""
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    workspace_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    client_request_id TEXT UNIQUE,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_runs_conversation
                    ON runs(conversation_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, seq),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                """
            )

    def create_conversation(self, title: str, workspace_path: str) -> dict[str, Any]:
        """创建会话并返回其元数据。"""

        conversation_id = f"conv_{uuid.uuid4().hex}"
        now = _now_text()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversations(
                    conversation_id, title, workspace_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, title, workspace_path, now, now),
            )
        return self.get_conversation(conversation_id)

    def list_conversations(self) -> list[dict[str, Any]]:
        """按更新时间倒序返回全部会话。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        """读取指定会话；不存在时抛出 KeyError。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            raise KeyError(conversation_id)
        return dict(row)

    def update_conversation(self, conversation_id: str, title: str) -> dict[str, Any]:
        """修改会话标题。"""

        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
                (title, _now_text(), conversation_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(conversation_id)
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> None:
        """只删除运行服务持有的会话与运行数据。"""

        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
            if cursor.rowcount == 0:
                raise KeyError(conversation_id)

    def create_run(self, snapshot: RunSnapshot, client_request_id: str) -> str:
        """创建运行；相同客户端请求返回已有 run_id。"""

        payload = snapshot.model_dump_json()
        now = _now_text()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT run_id FROM runs WHERE client_request_id = ?",
                (client_request_id,),
            ).fetchone()
            if existing:
                return str(existing["run_id"])
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, conversation_id, client_request_id,
                    snapshot_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.run.run_id,
                    snapshot.run.conversation_id,
                    client_request_id,
                    payload,
                    now,
                    now,
                ),
            )
        return snapshot.run.run_id

    def save_snapshot(self, snapshot: RunSnapshot) -> None:
        """原子更新运行快照。"""

        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE runs SET snapshot_json = ?, updated_at = ? WHERE run_id = ?",
                (snapshot.model_dump_json(), _now_text(), snapshot.run.run_id),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (_now_text(), snapshot.run.conversation_id),
            )

    def get_snapshot(self, run_id: str) -> RunSnapshot:
        """读取指定运行快照。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            raise KeyError(run_id)
        return RunSnapshot.model_validate_json(row["snapshot_json"])

    def list_snapshots(self) -> list[RunSnapshot]:
        """读取全部运行快照，用于服务重启恢复。"""

        with self._connect() as connection:
            rows = connection.execute("SELECT snapshot_json FROM runs").fetchall()
        return [RunSnapshot.model_validate_json(row["snapshot_json"]) for row in rows]

    def append_event(self, event: EventEnvelope) -> None:
        """持久化一条已分配序号的事件。"""

        payload = event.model_dump_json()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO events(run_id, seq, event_id, payload_json) VALUES (?, ?, ?, ?)",
                (event.run_id, event.seq, event.event_id, payload),
            )

    def events_after(self, run_id: str, after_seq: int, limit: int = 1000) -> list[EventEnvelope]:
        """读取某个运行指定序号之后的事件。"""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM events
                WHERE run_id = ? AND seq > ?
                ORDER BY seq ASC LIMIT ?
                """,
                (run_id, after_seq, limit),
            ).fetchall()
        return [EventEnvelope.model_validate_json(row["payload_json"]) for row in rows]

    def find_run_by_client_request(self, client_request_id: str) -> str | None:
        """按幂等请求 ID 查找既有运行。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM runs WHERE client_request_id = ?",
                (client_request_id,),
            ).fetchone()
        return str(row["run_id"]) if row else None
