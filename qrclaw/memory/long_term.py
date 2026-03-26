"""
中期记忆模块

使用 Markdown 文件存储重要信息，Agent 可以主动写入和读取。
特点：
- 持久化存储在本地文件
- Agent 可以主动管理记忆
- 启动时自动加载并注入到 system prompt
"""

from pathlib import Path
from datetime import datetime
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.long_term")


class LongTermMemory:
    """长期记忆管理器"""

    def __init__(self, memory_file: Path):
        """
        初始化长期记忆。

        Args:
            memory_file: 记忆文件路径（由 Workspace 提供）
        """
        self.memory_file = memory_file
        self._ensure_file()
        logger.info(f"中期记忆初始化: {self.memory_file}")

    def _ensure_file(self):
        """确保记忆文件存在"""
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            self.memory_file.write_text("# QRClaw 中期记忆\n\n", encoding="utf-8")
            logger.debug("创建中期记忆文件")

    def load(self) -> str:
        """
        加载记忆内容

        Returns:
            str: 记忆内容（Markdown 格式）
        """
        try:
            content = self.memory_file.read_text(encoding="utf-8")
            logger.info(f"加载中期记忆: {len(content)} 字符")
            return content
        except Exception as e:
            logger.error(f"加载中期记忆失败: {e}", exc_info=True)
            return ""

    def save(self, content: str) -> bool:
        """
        保存记忆内容（覆盖）

        Args:
            content: 要保存的内容

        Returns:
            bool: 是否成功
        """
        try:
            self.memory_file.write_text(content, encoding="utf-8")
            logger.info(f"保存中期记忆: {len(content)} 字符")
            return True
        except Exception as e:
            logger.error(f"保存中期记忆失败: {e}", exc_info=True)
            return False

    def append(self, content: str, title: str = None) -> bool:
        """
        追加记忆内容

        Args:
            content: 要追加的内容
            title: 可选的标题

        Returns:
            bool: 是否成功
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 构建追加内容
            append_text = f"\n---\n\n"
            if title:
                append_text += f"## {title}\n\n"
            append_text += f"**时间**: {timestamp}\n\n"
            append_text += f"{content}\n"

            # 追加到文件
            with open(self.memory_file, "a", encoding="utf-8") as f:
                f.write(append_text)

            logger.info(f"追加中期记忆: {title or '无标题'}, {len(content)} 字符")
            return True
        except Exception as e:
            logger.error(f"追加中期记忆失败: {e}", exc_info=True)
            return False

    def clear(self) -> bool:
        """
        清空记忆（保留标题）

        Returns:
            bool: 是否成功
        """
        try:
            self.memory_file.write_text("# QRClaw 中期记忆\n\n", encoding="utf-8")
            logger.info("清空中期记忆")
            return True
        except Exception as e:
            logger.error(f"清空中期记忆失败: {e}", exc_info=True)
            return False

    def exists(self) -> bool:
        """检查记忆文件是否存在"""
        return self.memory_file.exists()

    def size(self) -> int:
        """获取记忆文件大小（字符数）"""
        if self.exists():
            return len(self.load())
        return 0