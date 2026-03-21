import json
import os

# 会话文件统一存在这个目录下
SESSIONS_DIR = os.path.expanduser("~/.qrclaw/sessions")


class Session:
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self.messages: list[dict] = []

        # 确保目录存在
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        self._path = os.path.join(SESSIONS_DIR, f"{session_id}.json")

        # 启动时加载历史
        self._load()

    def add(self, message: dict):
        """追加一条消息，并立即存盘"""
        self.messages.append(message)
        self._save()

    def clear(self):
        """清空当前会话"""
        self.messages = []
        if os.path.exists(self._path):
            os.remove(self._path)

    def _save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self.messages, f, ensure_ascii=False, indent=2)

    def _load(self):
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 过滤掉旧历史里的 system 消息，system prompt 由 agent 实时生成
            self.messages = [m for m in data if m.get("role") != "system"]
