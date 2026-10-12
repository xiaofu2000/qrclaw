"""按会话持久化提取进度和待处理正文，避免重启、压缩后重复扫描。"""
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, TypeVar
import json
import math
import uuid

from filelock import FileLock

T = TypeVar("T")


@dataclass
class ExtractionState:
    """提取状态；seen 记录已接收版本，pending 保留尚未成功处理的正文。"""

    last_extraction_tokens: int = 0
    last_extraction_tool_calls: int = 0
    last_extraction_idx: int = 0
    total_extractions: int = 0
    last_extraction_time: float = 0.0
    last_attempt_time: float = 0.0
    last_activity_time: float = 0.0
    seen_messages: dict[str, str] = field(default_factory=dict)
    pending_messages: list[dict] = field(default_factory=list)
    preserved_message_ids: list[str] = field(default_factory=list)
    force_pending: bool = False


class ExtractionIndexer:
    """使用文件锁和原子替换维护单个会话的提取状态。"""

    def __init__(self, memory_dir: Path, session_id: str | None = None):
        """旧调用使用默认状态文件；真实会话使用独立文件，互不覆盖。"""
        self.memory_dir = Path(memory_dir)
        if session_id is None:
            self.state_file = self.memory_dir / ".extraction_state.json"
        else:
            if not session_id or any(c in session_id for c in "/\\:\r\n\0"):
                raise ValueError("无效的记忆提取会话 ID")
            self.state_file = self.memory_dir / ".extractions" / f"{session_id}.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(f"{self.state_file}.lock")
        self.worker_lock = FileLock(f"{self.state_file}.worker.lock")

    def get_state(self) -> ExtractionState:
        """每次读取磁盘最新状态，避免多个节点使用过期缓存。"""
        with self.lock:
            return self._load_state()

    def update(self, change: Callable[[ExtractionState], T]) -> T:
        """在锁内修改并落盘，回调应只执行短暂的状态操作。"""
        with self.lock:
            state = self._load_state()
            before = asdict(state)
            result = change(state)
            if asdict(state) != before:
                self._save_state(state)
            return result

    def record_extraction(self, tokens: int, tool_calls: int, message_count: int, current_time: float) -> None:
        """保留原有统计入口，记录一次成功处理。"""
        def change(state):
            state.last_extraction_tokens = tokens
            state.last_extraction_tool_calls = tool_calls
            state.last_extraction_idx = message_count
            state.total_extractions += 1
            state.last_extraction_time = current_time
        self.update(change)

    def record_message_idx(self, idx: int) -> None:
        """保存旧调用方使用的消息位置。"""
        self.update(lambda state: setattr(state, "last_extraction_idx", idx))

    def reset(self) -> None:
        """显式重置进度，不在读取异常时自动覆盖原文件。"""
        with self.lock:
            self._save_state(ExtractionState())

    def discard(self) -> None:
        """会话删除后清理其提取状态。"""
        with self.lock:
            self.state_file.unlink(missing_ok=True)

    def _load_state(self) -> ExtractionState:
        """读取并检查状态；损坏时明确报错，保留现场及待处理信息。"""
        if not self.state_file.exists():
            return ExtractionState()
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            state = ExtractionState(**data)
            if (not isinstance(state.seen_messages, dict)
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in state.seen_messages.items())
                    or not isinstance(state.pending_messages, list)
                    or not all(isinstance(m, dict) and isinstance(m.get("uuid"), str)
                               and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
                               for m in state.pending_messages)
                    or not isinstance(state.preserved_message_ids, list)
                    or not all(isinstance(i, str) for i in state.preserved_message_ids)
                    or not isinstance(state.force_pending, bool)
                    or not all(isinstance(getattr(state, name), (int, float))
                               and math.isfinite(getattr(state, name)) and getattr(state, name) >= 0
                               for name in ("last_extraction_time", "last_attempt_time", "last_activity_time", "total_extractions",
                                            "last_extraction_tokens", "last_extraction_tool_calls", "last_extraction_idx"))):
                raise ValueError("状态字段格式错误")
            return state
        except (ValueError, TypeError) as exc:
            raise ValueError(f"记忆提取进度损坏，请检查 {self.state_file}") from exc

    def _save_state(self, state: ExtractionState) -> None:
        """原子替换状态文件，写入失败时保留旧文件。"""
        temporary = self.state_file.with_name(f".{self.state_file.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.state_file)
        finally:
            temporary.unlink(missing_ok=True)
