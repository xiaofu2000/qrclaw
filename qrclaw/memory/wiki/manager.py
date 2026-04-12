"""
WikiMemoryManager —— Wiki 记忆系统管理器

替代原有的 MemoryManager，整合 WikiMemory + 兼容接口。
按 memory_dir 缓存单例，提供统一的记忆管理 API。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.wiki.manager")

# 全局单例缓存
_instances: dict[Path, "WikiMemoryManager"] = {}


class WikiMemoryManager:
    """
    Wiki 记忆系统管理器

    用法：
        manager = WikiMemoryManager.for_workspace(memory_dir)
        manager = WikiMemoryManager()  # 默认路径

    主要接口：
        - save_page: 新建或更新页面
        - get_page: 读取页面
        - search_pages: 搜索页面
        - load_index: 加载索引（system prompt 注入）
    """

    @classmethod
    def for_workspace(cls, memory_dir: Path) -> "WikiMemoryManager":
        """按 memory_dir 返回缓存实例，同一目录永远返回同一个对象"""
        key = Path(memory_dir).resolve()
        if key not in _instances:
            _instances[key] = cls(memory_dir)
        return _instances[key]

    def __init__(self, memory_dir: Path = None):
        from qrclaw.memory.wiki.wiki_memory import WikiMemory

        if memory_dir:
            self.memory_dir = Path(memory_dir)
        else:
            from qrclaw.memory.wiki.wiki_memory import AGENTS_ROOT
            self.memory_dir = AGENTS_ROOT / "default" / "memory"

        self.wiki = WikiMemory(self.memory_dir)
        logger.debug(f"WikiMemoryManager 初始化: {self.memory_dir}")

    # ── 兼容接口（对接 workspace.py）────────────────────────────────────────

    def save_page(
        self,
        name: str,
        content: str,
        description: str = "",
        tags: list[str] = None,
        related: list[str] = None,
    ):
        """保存页面（upsert 语义）"""
        return self.wiki.save_page(
            name=name,
            content=content,
            description=description,
            tags=tags or [],
            related=related or [],
        )

    def get_page(self, name: str):
        """读取页面"""
        return self.wiki.get_page(name)

    def list_pages(self):
        """列出所有页面"""
        return self.wiki.list_pages()

    def search_pages(self, query: str):
        """搜索页面"""
        return self.wiki.search_pages(query)

    def delete_page(self, name: str) -> bool:
        """删除页面"""
        return self.wiki.delete_page(name)

    def load_index(self) -> str:
        """加载索引（system prompt 注入用）"""
        return self.wiki.load_index()

    def rebuild_index(self):
        """重建索引"""
        self.wiki.rebuild_index()

    def clear_all(self):
        """清空所有页面"""
        self.wiki.clear_all()

    def stats(self) -> dict:
        """统计信息"""
        return self.wiki.stats()


# ── 别名兼容层 ───────────────────────────────────────────────────────────────

# 兼容旧 LongTermMemory 导入
LongTermMemory = WikiMemoryManager

__all__ = ["WikiMemoryManager", "LongTermMemory"]
