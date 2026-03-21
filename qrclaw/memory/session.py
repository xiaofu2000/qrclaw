import json
import os
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.session")

# 会话文件统一存在这个目录下
SESSIONS_DIR = os.path.expanduser("~/.qrclaw/sessions")


class Session:
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self.messages: list[dict] = []
        
        # 上下文使用情况
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

        # 确保目录存在
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        self._path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        
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
        if os.path.exists(self._path):
            os.remove(self._path)
            logger.debug(f"删除会话文件: {self._path}")

    def _save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, ensure_ascii=False, indent=2)
            logger.debug(f"会话保存成功: {self._path}")
        except Exception as e:
            logger.error(f"会话保存失败: {e}", exc_info=True)
            raise

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 过滤掉旧历史里的 system 消息，system prompt 由 agent 实时生成
                self.messages = [m for m in data if m.get("role") != "system"]
                logger.info(f"加载历史会话: {len(self.messages)} 条消息")
            except Exception as e:
                logger.error(f"加载会话失败: {e}", exc_info=True)
                self.messages = []
        else:
            logger.debug("未找到历史会话，创建新会话")