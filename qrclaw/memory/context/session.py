import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from qrclaw.logger import get_logger
from qrclaw.memory.token_utils import count_messages_tokens

logger = get_logger("qrclaw.memory.session")


def _session_path(sessions_dir: Path, session_id: str) -> Path:
    """校验会话标识，禁止越过会话目录读写。"""
    if not session_id.strip() or any(c in session_id for c in "/\\\x00\n\r") or session_id in {".", ".."}:
        raise ValueError("会话 ID 不能为空或包含路径分隔符、换行")
    path = sessions_dir / f"{session_id}.json"
    if path.resolve().parent != sessions_dir.resolve():
        raise ValueError("会话文件不能指向会话目录之外")
    return path


def _session_files(sessions_dir: Path) -> list[Path]:
    """按修改时间倒序列出主会话，排除子任务。"""
    return sorted(
        (p for p in sessions_dir.glob("*.json") if not p.stem.startswith("sub-")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )


def list_sessions(sessions_dir: Path) -> list[dict]:
    """
    列出指定目录下所有已保存的会话。

    Args:
        sessions_dir: 会话文件目录（由 Workspace 提供）
    Returns:
        list[dict]: 每项包含 id、message_count、updated_at
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sessions = []
    for path in _session_files(sessions_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
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
    """
    获取最近使用的会话 ID（按修改时间排序，取最新的）。
    过滤掉子 agent 产生的临时 session（sub- 开头）。

    Args:
        sessions_dir: 会话文件目录
    Returns:
        str | None: 会话 ID，如果没有会话则返回 None
    """
    sessions = _session_files(sessions_dir)
    return sessions[0].stem if sessions else None


def delete_session(session_id: str, sessions_dir: Path) -> bool:
    """删除指定会话文件，返回是否成功。"""
    path = _session_path(sessions_dir, session_id)
    if path.exists():
        path.unlink()
        logger.info(f"删除会话: {session_id}")
        return True
    return False


def _recover_interrupted_tools(messages: list[dict]) -> list[dict]:
    """恢复进程中断留下的未配对调用；已有工具结果原样保留。"""
    repaired, pending = [], {}
    for message in [*messages, None]:
        if message is None or message["role"] != "tool":
            for call_id in pending:
                repaired.append({
                    "role": "tool", "tool_call_id": call_id,
                    "content": "上次运行中断，未保存工具结果，请检查实际状态后继续。",
                    "uuid": uuid.uuid4().hex[:12],
                })
            pending = {}
        if message is None:
            break
        if message["role"] == "assistant":
            pending = {call["id"]: call for call in message.get("tool_calls") or []}
        elif message["role"] == "tool":
            pending.pop(message.get("tool_call_id"), None)
        repaired.append(message)
    return repaired


class Session:
    """维护可恢复的会话消息，所有持久化更新均采用原子替换。"""

    def __init__(self, sessions_dir: Path, session_id: str = None, resume: bool = True):
        """
        初始化会话。

        Args:
            sessions_dir: 会话文件目录（由 Workspace 提供）
            session_id: 指定会话 ID，为 None 时自动选择
            resume: 是否恢复最近的会话（仅在 session_id 为 None 时生效）
        """
        self.session_id = session_id if session_id is not None else (get_last_session_id(sessions_dir) if resume else None)
        if self.session_id is None:
            self.session_id = f"{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:8]}"

        self.messages: list[dict] = []

        # 上下文使用情况
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

        # 会话文件路径（由 Workspace 提供的目录决定）
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._path = _session_path(sessions_dir, self.session_id)

        logger.debug(f"初始化会话: {self.session_id}, 路径: {self._path}")

        # 启动时加载历史
        self._load()

    def add(self, message: dict):
        """追加消息并原子存盘；失败时保留内存和磁盘中的原会话。"""
        messages = _recover_interrupted_tools(self.messages) if message.get("role") == "user" else self.messages
        self.replace_messages([*messages, message])

    def replace_messages(self, messages: list[dict]) -> None:
        """统一提交追加或压缩后的消息，保留已有 UUID。"""
        prepared = [dict(message, uuid=message.get("uuid") or uuid.uuid4().hex[:12]) for message in messages]
        tokens = count_messages_tokens(prepared)
        self._save(prepared)
        self.messages = prepared
        self.prompt_tokens = tokens

    def update_tokens(self, prompt_tokens: int, completion_tokens: int, total_tokens: int):
        """更新 token 使用情况"""
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        logger.debug(f"更新 token: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")

    def clear(self):
        """清空当前会话"""
        logger.info(f"清除会话: {self.session_id}")
        self._path.unlink(missing_ok=True)
        self.messages = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def _save(self, messages: list[dict]) -> None:
        """写入同目录临时文件后原子替换，写入中断不会截断原文件。"""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._path.parent, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(messages, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _load(self) -> None:
        """加载历史；损坏时明确报错，禁止当作空会话覆盖。"""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, list) or any(
                not isinstance(m, dict) or m.get("role") not in {"system", "user", "assistant", "tool"}
                or not isinstance(m.get("content", ""), (str, list, type(None)))
                for m in data
            ):
                raise ValueError("消息格式无效")
            self.messages = [dict(m, uuid=m.get("uuid") or uuid.uuid4().hex[:12]) for m in data if m["role"] != "system"]
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"会话文件损坏，已保留原文件：{self._path}") from exc
        self.prompt_tokens = count_messages_tokens(self.messages)
        logger.info(f"加载历史会话：{len(self.messages)} 条消息")
