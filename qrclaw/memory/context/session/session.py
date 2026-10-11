import json
import uuid
from datetime import datetime
from pathlib import Path

from qrclaw.logger import get_logger
from qrclaw.memory.context.session.events import SessionEvent
from qrclaw.memory.token_utils import count_messages_tokens

logger = get_logger("qrclaw.memory.session")


def _normalize_message(message: dict) -> dict:
    if "kind" not in message:
        role = message.get("role")
        if role == "user":
            message["kind"] = "user"
        elif role == "assistant":
            message["kind"] = "assistant"
        elif role == "tool":
            message["kind"] = "tool"
        else:
            message["kind"] = "message"
    return message


def list_sessions(sessions_dir: Path) -> list[dict]:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sessions = []
    for path in sorted(
        [p for p in sessions_dir.glob("*.json") if not p.stem.startswith("sub-")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw_messages = data.get("messages", [])
                msg_count = len([m for m in raw_messages if m.get("role") != "system"])
            else:
                msg_count = len([m for m in data if m.get("role") != "system"])
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            sessions.append({
                "id": path.stem,
                "message_count": msg_count,
                "updated_at": mtime,
            })
        except Exception:
            pass
    return sessions


def get_last_session_id(sessions_dir: Path) -> str | None:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sessions = sorted(
        [p for p in sessions_dir.glob("*.json") if not p.stem.startswith("sub-")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if sessions:
        return sessions[0].stem
    return None


def delete_session(session_id: str, sessions_dir: Path) -> bool:
    path = sessions_dir / f"{session_id}.json"
    if path.exists():
        path.unlink()
        logger.info(f"删除会话: {session_id}")
        return True
    return False


def count_tokens(messages: list[dict]) -> int:
    return count_messages_tokens(messages)


class Session:
    def __init__(self, sessions_dir: Path, session_id: str = None, resume: bool = True):
        if session_id is not None:
            self.session_id = session_id
        elif resume:
            last_id = get_last_session_id(sessions_dir)
            if last_id:
                self.session_id = last_id
                logger.info(f"恢复最近的会话: {last_id}")
            else:
                short = uuid.uuid4().hex[:8]
                self.session_id = f"{datetime.now().strftime('%Y%m%d')}-{short}"
                logger.info(f"创建新会话: {self.session_id}")
        else:
            short = uuid.uuid4().hex[:8]
            self.session_id = f"{datetime.now().strftime('%Y%m%d')}-{short}"
            logger.info(f"创建新会话: {self.session_id}")

        self.messages: list[dict] = []
        self.events: list[dict] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._path = sessions_dir / f"{self.session_id}.json"
        self._load()

    def add(self, message: dict):
        message = _normalize_message(dict(message))
        if "uuid" not in message:
            message["uuid"] = uuid.uuid4().hex[:12]
        self.messages.append(message)
        self.prompt_tokens = count_messages_tokens(self.messages)
        self._save()

    def add_event(self, event: SessionEvent | dict):
        payload = event.__dict__ if isinstance(event, SessionEvent) else dict(event)
        payload.setdefault("created_at", datetime.now().isoformat())
        self.events.append(payload)
        self._save()

    def update_tokens(self, prompt_tokens: int, completion_tokens: int, total_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

    def clear(self):
        self.messages = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.events = []
        if self._path.exists():
            self._path.unlink()

    def _save(self):
        try:
            self._path.write_text(
                json.dumps({"messages": self.messages, "events": self.events}, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"会话保存失败: {e}", exc_info=True)
            raise

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    messages = data.get("messages", [])
                    events = data.get("events", [])
                else:
                    messages = data
                    events = []
                self.messages = [_normalize_message(m) for m in messages if m.get("role") != "system"]
                self.events = [dict(event) for event in events]
                self.prompt_tokens = count_messages_tokens(self.messages)
            except Exception as e:
                logger.error(f"加载会话失败: {e}", exc_info=True)
                self.messages = []
