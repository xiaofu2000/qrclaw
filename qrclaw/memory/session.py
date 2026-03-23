import json
import uuid
from datetime import datetime
from pathlib import Path
from qrclaw.logger import get_logger

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
    for path in sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
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


def delete_session(session_id: str, sessions_dir: Path) -> bool:
    """删除指定会话文件，返回是否成功。"""
    path = sessions_dir / f"{session_id}.json"
    if path.exists():
        path.unlink()
        logger.info(f"删除会话: {session_id}")
        return True
    return False


class Session:
    def __init__(self, sessions_dir: Path, session_id: str = None):
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

        # 当前活跃计划
        self.active_plan: dict | None = None

        # 会话文件路径（由 Workspace 提供的目录决定）
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._path = sessions_dir / f"{session_id}.json"

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

    def set_plan(self, goal: str, steps: list[dict]):
        """设置当前活跃计划"""
        self.active_plan = {
            "goal": goal,
            "steps": [{"id": s["id"], "description": s["description"], "done": False} for s in steps],
        }
        logger.info(f"设置执行计划: {goal}, 共 {len(steps)} 步")

    def complete_step(self, step_id: int) -> bool:
        """标记某步骤为已完成，返回是否全部完成"""
        if not self.active_plan:
            return False
        for step in self.active_plan["steps"]:
            if step["id"] == step_id:
                step["done"] = True
                logger.info(f"计划步骤 {step_id} 已完成")
                break
        all_done = all(s["done"] for s in self.active_plan["steps"])
        if all_done:
            logger.info("所有计划步骤已完成，清空计划")
            self.active_plan = None
        return all_done

    def clear_plan(self):
        """清空当前计划"""
        self.active_plan = None
        logger.info("计划已清空")

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