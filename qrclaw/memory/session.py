import json
import uuid
from datetime import datetime
from pathlib import Path
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.session")

# 会话文件统一存在这个目录下
SESSIONS_DIR = Path.home() / ".qrclaw" / "sessions"


def list_sessions() -> list[dict]:
    """
    列出所有已保存的会话。

    Returns:
        list[dict]: 每项包含 id、message_count、updated_at（文件修改时间）
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for path in sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
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


def delete_session(session_id: str) -> bool:
    """删除指定会话文件，返回是否成功。"""
    path = SESSIONS_DIR / f"{session_id}.json"
    if path.exists():
        path.unlink()
        logger.info(f"删除会话: {session_id}")
        return True
    return False


class Session:
    def __init__(self, session_id: str = None):
        # 不传 session_id 时自动生成，格式：YYYYMMDD-<uuid4 前8位>
        if session_id is None:
            short = uuid.uuid4().hex[:8]
            session_id = f"{datetime.now().strftime('%Y%m%d')}-{short}"
        self.session_id = session_id
        self.messages: list[dict] = []

        # 上下文使用情况
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

        # 确保目录存在
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._path = SESSIONS_DIR / f"{session_id}.json"

        logger.debug(f"初始化会话: {session_id}, 路径: {self._path}")

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
                logger.info(f"加载历史会话: {len(self.messages)} 条消息")
            except Exception as e:
                logger.error(f"加载会话失败: {e}", exc_info=True)
                self.messages = []
        else:
            logger.debug("未找到历史会话，创建新会话")