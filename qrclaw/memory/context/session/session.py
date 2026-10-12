import json
import uuid
from collections.abc import Callable
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
            title = (data.get("title") if isinstance(data, dict) else None) or " ".join(first_user_text.split())[:48] or "新对话"
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


def delete_session(session_id: str, sessions_dir: Path, *, before_delete: Callable[[], None] | None = None) -> bool:
    """在会话文件锁内先执行来源撤销，再删除文件。"""
    path = _session_path(sessions_dir, session_id)
    with FileLock(f"{path}.lock"):
        if path.exists():
            if before_delete is not None:
                before_delete()
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
        self.title: str | None = None
        self.history_version = 0
        self.prompt_tokens = 0
        self.context_overhead_tokens = 0
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
            if message.get("role") == "user":
                message.setdefault("turn_event_index", len(self.events))
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

    def set_title(self, title: str) -> None:
        """保存用户指定的会话标题。"""
        with FileLock(f"{self._path}.lock"):
            if not self._path.exists():
                raise FileNotFoundError(self.session_id)
            self._load(strict=True)
            self.title = title
            self._save()

    def rewrite_latest_user_turn(self, message_id: str, content: str | None, *, before_save: Callable[[str, set[str], set[str]], None] | None = None) -> set[str]:
        """原子替换或删除最后一轮用户消息，并返回需清理的子会话 ID。"""
        with FileLock(f"{self._path}.lock"):
            if not self._path.exists():
                raise FileNotFoundError(self.session_id)
            self._load(strict=True)
            index = next((i for i in range(len(self.messages) - 1, -1, -1)
                          if self.messages[i].get("role") == "user"), -1)
            if index < 0 or self.messages[index].get("uuid") != message_id:
                raise ValueError("只能操作最后一条用户消息，请刷新会话")
            original = self.messages[index]
            removed_ids = {m["uuid"] for m in self.messages[index:] if m.get("uuid")}
            if "turn_event_index" in original:
                event_start = int(original["turn_event_index"])
            else:
                # shortcut: 旧会话仅用完整保存事件定位，缺失时清空过程；需保留时迁移轮次索引。
                prior_messages = sum(message.get("role") in ("assistant", "tool") and message.get("kind") != "execution_report"
                                     for message in self.messages[:index])
                saved_events = [i for i, event in enumerate(self.events) if event.get("kind") == "message_saved"]
                total_messages = sum(message.get("role") in ("assistant", "tool") and message.get("kind") != "execution_report"
                                     for message in self.messages)
                event_start = saved_events[prior_messages - 1] + 1 if len(saved_events) == total_messages and prior_messages > 0 else 0
            event_start = max(0, min(event_start, len(self.events)))
            removed_events = self.events[event_start:]
            children = {
                child_id for event in removed_events if event.get("kind") == "subagent"
                if (child_id := (event.get("meta") or {}).get("child_session_id"))
            }
            self.messages = self.messages[:index]
            self.events = self.events[:event_start]
            if content is not None:
                self.messages.append({**original, "content": content, "turn_event_index": len(self.events)})
            self.history_version += 1
            self.prompt_tokens = count_messages_tokens(self.messages)
            try:
                self._save(before_replace=(lambda text: before_save(text, removed_ids, children)) if before_save is not None else None)
            except Exception:
                self._load(strict=True)
                raise
            return children

    def start_run_usage(self):
        self.run_usage = dict.fromkeys(USAGE_KEYS, 0)

    @property
    def context_tokens(self) -> int:
        """返回当前历史和已加载提示词、工具定义的上下文用量估算。"""
        return self.prompt_tokens + self.context_overhead_tokens

    def set_context_overhead(self, tokens: int) -> None:
        """保存本次执行的提示词和工具开销，供会话查询复用。"""
        with FileLock(f"{self._path}.lock"):
            self._load(strict=True)
            self.context_overhead_tokens = max(0, tokens)
            self._save()

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
            self.context_overhead_tokens = 0
            self.completion_tokens = 0
            self.total_tokens = 0
            self.usage = dict.fromkeys(USAGE_KEYS, 0)
            self.run_usage = dict.fromkeys(USAGE_KEYS, 0)
            self.events = []
            self.title = None
            self.history_version = 0
            if self._path.exists():
                self._path.unlink()

    def _save(self, *, before_replace: Callable[[str], None] | None = None):
        """原子保存；可在替换前持久化与聊天删改绑定的来源撤销。"""
        temp_path = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            text = json.dumps({"messages": self.messages, "events": self.events, "usage": self.usage,
                            "title": self.title, "history_version": self.history_version,
                            "context_overhead_tokens": self.context_overhead_tokens}, ensure_ascii=False, indent=2)
            temp_path.write_text(text, encoding="utf-8")
            if before_replace is not None:
                before_replace(text)
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
            self.title = None
            self.history_version = 0
            self.prompt_tokens = 0
            self.context_overhead_tokens = 0
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
                title = data.get("title")
                history_version = data.get("history_version", 0)
                context_overhead_tokens = data.get("context_overhead_tokens", 0)
            else:
                messages = data
                events = []
                usage = {}
                title = None
                history_version = 0
                context_overhead_tokens = 0
            self.messages = [_normalize_message(m) for m in messages if m.get("role") != "system"]
            self.events = [dict(event) for event in events]
            self.title = title if isinstance(title, str) and title else None
            self.history_version = int(history_version)
            self.prompt_tokens = count_messages_tokens(self.messages)
            self.context_overhead_tokens = max(0, int(context_overhead_tokens))
            self.usage = {key: int(usage.get(key, 0)) for key in USAGE_KEYS}
            self.completion_tokens = self.usage["completion_tokens"]
            self.total_tokens = self.usage["total_tokens"]
        except Exception as e:
            logger.error(f"加载会话失败: {e}", exc_info=True)
            if strict:
                raise
            self.messages = []
            self.events = []
            self.title = None
            self.history_version = 0
            self.context_overhead_tokens = 0
            self.usage = dict.fromkeys(USAGE_KEYS, 0)
            self.prompt_tokens = self.completion_tokens = self.total_tokens = 0
