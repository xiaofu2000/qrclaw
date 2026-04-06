"""
中期记忆模块

基于 MemoryManager 的结构化存储，支持四种记忆类型。

目录结构：
    ~/.qrclaw/agents/{agent_id}/memory/
    ├── MEMORY.md              # 入口索引
    ├── user/                  # 用户记忆（私有）
    │   └── <name>.md
    ├── feedback/              # 反馈记忆
    │   └── <name>.md
    ├── project/               # 项目记忆
    │   └── <name>.md
    └── reference/             # 外部引用
        └── <name>.md
"""
from pathlib import Path
from qrclaw.logger import get_logger
from qrclaw.memory.types import MemoryType

logger = get_logger("qrclaw.memory.long_term")

# 统一的 agents 目录结构
AGENTS_ROOT = Path.home() / ".qrclaw" / "agents"


class LongTermMemory:
    """
    长期记忆管理器
    
    封装 MemoryManager 和 MemoryIndexer，提供简洁的接口。
    """

    def __init__(self, memory_dir: Path = None):
        """
        初始化长期记忆。

        Args:
            memory_dir: 记忆目录路径，默认为 ~/.qrclaw/agents/default/memory/
        """
        if memory_dir:
            self.memory_dir = Path(memory_dir)
        else:
            self.memory_dir = AGENTS_ROOT / "default" / "memory"
        
        # 延迟导入
        self._manager = None
        self._indexer = None
        
        logger.debug(f"中期记忆初始化: {self.memory_dir}")

    @property
    def manager(self):
        """懒加载 MemoryManager"""
        if self._manager is None:
            from qrclaw.memory.core.memory_manager import MemoryManager
            self._manager = MemoryManager(self.memory_dir)
            logger.debug("MemoryManager 初始化完成")
        return self._manager

    @property
    def indexer(self):
        """懒加载 MemoryIndexer"""
        if self._indexer is None:
            from qrclaw.memory.storage.indexer import MemoryIndexer
            self._indexer = MemoryIndexer(self.memory_dir)
            logger.debug("MemoryIndexer 初始化完成")
        return self._indexer

    def load(self) -> str:
        """
        加载记忆索引内容

        Returns:
            str: MEMORY.md 内容
        """
        try:
            return self.indexer.get_index()
        except Exception as e:
            logger.error(f"加载中期记忆失败: {e}", exc_info=True)
            return ""

    def append(self, content: str, title: str = None, memory_type: MemoryType = None) -> bool:
        """
        追加记忆

        Args:
            content: 内容
            title: 标题
            memory_type: 类型，默认 project

        Returns:
            bool: 是否成功
        """
        from datetime import datetime
        mtype = memory_type or MemoryType.PROJECT
        
        return self.save_entry(
            name=title or f"记忆_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            description=title or "无标题",
            content=content,
            memory_type=mtype,
        )

    def save_entry(
        self,
        name: str,
        content: str,
        memory_type: MemoryType = MemoryType.PROJECT,
        description: str = "",
    ) -> bool:
        """
        保存记忆

        Args:
            name: 记忆名称
            content: 内容
            memory_type: 类型
            description: 描述

        Returns:
            bool: 是否成功
        """
        try:
            success = self.manager.save_memory(
                name=name,
                description=description,
                content=content,
                memory_type=memory_type,
            )
            if success:
                logger.info(f"保存记忆: {name}")
            return success
        except Exception as e:
            logger.error(f"保存记忆失败: {e}", exc_info=True)
            return False

    def clear(self) -> bool:
        """清空所有记忆"""
        try:
            self.manager.clear()
            self.indexer.rebuild()
            logger.info("清空中期记忆")
            return True
        except Exception as e:
            logger.error(f"清空中期记忆失败: {e}", exc_info=True)
            return False

    def exists(self) -> bool:
        """检查记忆目录是否存在"""
        return self.memory_dir.exists()

    def size(self) -> int:
        """获取记忆索引大小"""
        return len(self.load())
