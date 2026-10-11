import json
import uuid
from datetime import datetime
from pathlib import Path
from filelock import FileLock

from qrclaw.logger import get_logger
from qrclaw.memory.context.session.events import SessionEvent
from qrclaw.memory.token_utils import count_messages_tokens

logger = get_logger("qrclaw.memory.session")
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _session_path(sessions_dir: Path, session_id: str) -> Path:
    if not isinstance(session_id, str) or not session_id.strip() or session_id != session_id.strip() or any(c in session_id for c in "/\\:\r\n\0"):
        raise ValueError(f"无效的会话 ID: {session_id!r}")
    path = sessions_dir / f"{session_id}.json"
    if not path.resolve().is_relative_to(sessions_dir.resolve()):
        raise ValueError(f"无效的会话 ID: {session_id!r}")
    return path


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
            raw_messages = data.get("messages", []) if isinstance(data, dict) else data
            messages = [m for m in raw_messages if m.get("role") != "system"]
            first_user_text = next((m.get("content") for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
            title = " ".join(first_user_text.split())[:48] or path.stem
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            sessions.append({
                "id": path.stem,
                "title": title,
                "message_count": len(messages),
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
    path = _session_path(sessions_dir, session_id)
    with FileLock(f"{path}.lock"):
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
        self.usage = dict.fromkeys(USAGE_KEYS, 0)
        self.run_usage = dict.fromkeys(USAGE_KEYS, 0)

        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._path = _session_path(sessions_dir, self.session_id)
        self._load()

    def add(self, message: dict):
        with FileLock(f"{self._path}.lock"):
            self._load(strict=True)
            message = _normalize_message(dict(message))
            if "uuid" not in message:
                message["uuid"] = uuid.uuid4().hex[:12]
            self.messages.append(message)
            self.prompt_tokens = count_messages_tokens(self.messages)
            self._save()

    def add_event(self, event: SessionEvent | dict):
        with FileLock(f"{self._path}.lock"):
            self._load(strict=True)
            payload = event.__dict__ if isinstance(event, SessionEvent) else dict(event)
            payload.setdefault("created_at", datetime.now().isoformat())
            self.events.append(payload)
            self._save()

    def ensure_saved(self):
        with FileLock(f"{self._path}.lock"):
            if not self._path.exists():
                self._save()

    def start_run_usage(self):
        self.run_usage = dict.fromkeys(USAGE_KEYS, 0)

    def update_tokens(self, prompt_tokens: int, completion_tokens: int, total_tokens: int):
        values = (prompt_tokens, completion_tokens, total_tokens)
        with FileLock(f"{self._path}.lock"):
            self._load(strict=True)
            for key, value in zip(USAGE_KEYS, values):
                self.usage[key] += value
                self.run_usage[key] += value
            self.completion_tokens = self.usage["completion_tokens"]
            self.total_tokens = self.usage["total_tokens"]
            self._save()

    def clear(self):
        with FileLock(f"{self._path}.lock"):
            self.messages = []
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.total_tokens = 0
            self.usage = dict.fromkeys(USAGE_KEYS, 0)
            self.run_usage = dict.fromkeys(USAGE_KEYS, 0)
            self.events = []
            if self._path.exists():
                self._path.unlink()

    def _save(self):
        temp_path = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps({"messages": self.messages, "events": self.events, "usage": self.usage}, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            temp_path.replace(self._path)
        except Exception as e:
            logger.error(f"会话保存失败: {e}", exc_info=True)
            raise
        finally:
            temp_path.unlink(missing_ok=True)

    def _load(self, strict: bool = False):
        if not self._path.exists():
            self.messages = []
            self.events = []
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.total_tokens = 0
            self.usage = dict.fromkeys(USAGE_KEYS, 0)
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                messages = data.get("messages", [])
                events = data.get("events", [])
                usage = data.get("usage", {})
            else:
                messages = data
                events = []
                usage = {}
            self.messages = [_normalize_message(m) for m in messages if m.get("role") != "system"]
            self.events = [dict(event) for event in events]
            self.prompt_tokens = count_messages_tokens(self.messages)
            self.usage = {key: int(usage.get(key, 0)) for key in USAGE_KEYS}
            self.completion_tokens = self.usage["completion_tokens"]
            self.total_tokens = self.usage["total_tokens"]
        except Exception as e:
            logger.error(f"加载会话失败: {e}", exc_info=True)
            if strict:
                raise
            self.messages = []
            self.events = []
            self.usage = dict.fromkeys(USAGE_KEYS, 0)
            self.prompt_tokens = self.completion_tokens = self.total_tokens = 0
