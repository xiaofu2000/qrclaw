import json
import uuid
from datetime import datetime
from pathlib import Path
from qrclaw.logger import get_logger
from qrclaw.memory.token_utils import count_messages_tokens

logger = get_logger("qrclaw.memory.session")


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
    for path in sorted(
        [p for p in sessions_dir.glob("*.json") if not p.stem.startswith("sub-")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
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
    """删除指定会话文件，返回是否成功。"""
    path = sessions_dir / f"{session_id}.json"
    if path.exists():
        path.unlink()
        logger.info(f"删除会话: {session_id}")
        return True
    return False



class Session:
    def __init__(self, sessions_dir: Path, session_id: str = None, resume: bool = True):
        """
        初始化会话。

        Args:
            sessions_dir: 会话文件目录（由 Workspace 提供）
            session_id: 指定会话 ID，为 None 时自动选择
            resume: 是否恢复最近的会话（仅在 session_id 为 None 时生效）
        """
        # 如果指定了 session_id，直接使用
        if session_id is not None:
            self.session_id = session_id
        # 否则根据 resume 决定是恢复最近会话还是创建新会话
        elif resume:
            # 尝试恢复最近的会话
            last_id = get_last_session_id(sessions_dir)
            if last_id:
                self.session_id = last_id
                logger.info(f"恢复最近的会话: {last_id}")
            else:
                # 没有历史会话，创建新的
                short = uuid.uuid4().hex[:8]
                self.session_id = f"{datetime.now().strftime('%Y%m%d')}-{short}"
                logger.info(f"创建新会话: {self.session_id}")
        else:
            # 不恢复，创建新会话
            short = uuid.uuid4().hex[:8]
            self.session_id = f"{datetime.now().strftime('%Y%m%d')}-{short}"
            logger.info(f"创建新会话: {self.session_id}")

        self.messages: list[dict] = []

        # 上下文使用情况
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

        # 会话文件路径（由 Workspace 提供的目录决定）
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._path = sessions_dir / f"{self.session_id}.json"

        logger.debug(f"初始化会话: {self.session_id}, 路径: {self._path}")

        # 启动时加载历史
        self._load()

    def add(self, message: dict):
        """追加一条消息，并立即存盘"""
        self.messages.append(message)
        self._save()
        logger.debug(f"添加消息: {message.get('role', 'unknown')}, 当前会话消息数: {len(self.messages)}")

    def update_tokens(self, prompt_tokens: int, completion_tokens: int, total_tokens: int):
        """更新 token 使用情况"""
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        logger.debug(f"更新 token: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")

    def clear(self):
        """清空当前会话"""
        logger.info(f"清除会话: {self.session_id}")
        self.messages = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        if self._path.exists():
            self._path.unlink()
            logger.debug(f"删除会话文件: {self._path}")

    def _save(self):
        try:
            self._path.write_text(
                json.dumps(self.messages, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            logger.debug(f"会话保存成功: {self._path}")
        except Exception as e:
            logger.error(f"会话保存失败: {e}", exc_info=True)
            raise

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                # 过滤掉旧历史里的 system 消息，system prompt 由 agent 实时生成
                self.messages = [m for m in data if m.get("role") != "system"]
                # 精确计算已加载消息的 token 数
                self.prompt_tokens = count_messages_tokens(self.messages)
                logger.info(f"加载历史会话: {len(self.messages)} 条消息, {self.prompt_tokens} tokens")
            except Exception as e:
                logger.error(f"加载会话失败: {e}", exc_info=True)
                self.messages = []
        else:
            logger.debug("未找到历史会话，创建新会话")
