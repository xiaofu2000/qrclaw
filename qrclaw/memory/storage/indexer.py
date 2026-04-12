"""
MemoryIndexer —— 记忆索引管理器

核心职责：
1. 管理 MEMORY.md 索引文件（入口文件）
2. 提供增量更新和全量重建能力
3. 缓存索引内容，避免重复读取文件

设计说明：
- 增量更新：save_memory/delete_memory 时调用 update_entrypoint/remove_entry
- 全量重建：批量操作后调用 rebuild()
- 缓存：避免频繁读取 MEMORY.md 文件
- 索引管理逻辑直接内嵌，不依赖 MemoryManager（已迁移到 wiki 模块）

迁移说明：
- 原有的 core/memory_manager 依赖已移除
- 改用 WikiMemoryManager 的 IndexManager 进行索引管理
"""
import re
from pathlib import Path
from typing import Optional

from qrclaw.memory.types import MemoryFile, MemoryType
from qrclaw.memory.wiki import WikiMemoryManager
from qrclaw.memory.wiki.page import WikiPage
from qrclaw.memory.wiki.index import IndexManager
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.indexer")

# 限制常量
MAX_ENTRYPOINT_LINES = 200


class MemoryIndexer:
    """
    记忆索引管理器
    
    使用方式：
    ```python
    indexer = MemoryIndexer(memory_dir)
    
    # 增量更新（单条操作）
    indexer.update_entrypoint(memory)
    
    # 全量重建（批量操作后）
    indexer.rebuild()
    
    # 获取索引
    content = indexer.get_index()
    ```
    """

    def __init__(self, memory_dir: Path):
        """
        初始化 MemoryIndexer
        
        Args:
            memory_dir: 记忆根目录
        """
        self.memory_dir = memory_dir
        
        # 索引缓存
        self._cached_index: Optional[str] = None
        self._cached_mtime: float = 0.0
        
        # WikiMemoryManager 实例（懒加载）
        self._wiki: Optional[WikiMemoryManager] = None
        
        # IndexManager 实例（懒加载）
        self._index_manager: Optional[IndexManager] = None
        
        logger.debug(f"MemoryIndexer 初始化: {memory_dir}")

    @property
    def wiki(self) -> WikiMemoryManager:
        """获取 WikiMemoryManager 实例（懒加载）"""
        if self._wiki is None:
            self._wiki = WikiMemoryManager.for_workspace(self.memory_dir)
        return self._wiki

    @property
    def index_manager(self) -> IndexManager:
        """获取 IndexManager 实例（懒加载）"""
        if self._index_manager is None:
            self._index_manager = IndexManager(self.memory_dir)
        return self._index_manager

    # ── 索引获取 ─────────────────────────────────────────────────────

    def get_index(self, force_rebuild: bool = False) -> str:
        """
        获取 MEMORY.md 索引内容
        
        Args:
            force_rebuild: 是否强制重建
            
        Returns:
            MEMORY.md 的内容字符串
        """
        if force_rebuild:
            self.rebuild()
        
        return self._load_from_file()

    # ── 增量更新 ─────────────────────────────────────────────────────

    def update_entrypoint(self, memory: MemoryFile) -> bool:
        """
        更新单条索引条目（增量更新）
        
        Args:
            memory: MemoryFile 实例
            
        Returns:
            bool: 是否成功
        """
        try:
            # 将 MemoryFile 转换为 WikiPage
            page = WikiPage(
                name=memory.name,
                content=memory.content,
                description=memory.description or "",
                tags=self._memory_type_to_tags(memory.type),
                related=[],
                created_at=memory.created_at,
                updated_at=memory.updated_at,
            )
            # 使用 WikiMemoryManager 保存页面
            self.wiki.save_page(page)
            # 使缓存失效
            self._invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"更新单条索引失败: {e}", exc_info=True)
            return False

    def remove_entry(self, name: str) -> bool:
        """
        移除单条索引条目
        
        Args:
            name: 记忆名称
            
        Returns:
            bool: 是否成功
        """
        try:
            # 直接使用 IndexManager.remove() 删除索引
            self.index_manager.remove(name)
            # 同时删除页面文件
            self.wiki.delete_page(name)
            # 使缓存失效
            self._invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"移除索引条目失败: {e}", exc_info=True)
            return False

    # ── 全量重建 ─────────────────────────────────────────────────────

    def rebuild(self) -> bool:
        """
        全量重建 MEMORY.md 索引
        
        扫描所有记忆文件，重新生成完整索引。
        用于批量操作后。
        
        Returns:
            bool: 是否成功
        """
        try:
            # 直接使用 IndexManager.rebuild_from_pages_dir()
            self.index_manager.rebuild_from_pages_dir()
            # 使缓存失效
            self._invalidate_cache()
            logger.info("索引全量重建完成")
            return True
        except Exception as e:
            logger.error(f"索引重建失败: {e}", exc_info=True)
            return False

    # ── 辅助方法 ─────────────────────────────────────────────────────

    @staticmethod
    def _memory_type_to_tags(memory_type: MemoryType) -> list[str]:
        """将 MemoryType 转换为 tags"""
        type_to_tag = {
            MemoryType.USER: ["用户"],
            MemoryType.FEEDBACK: ["反馈"],
            MemoryType.PROJECT: ["项目"],
            MemoryType.REFERENCE: ["参考"],
        }
        return type_to_tag.get(memory_type, ["项目"])

    # ── 缓存管理 ─────────────────────────────────────────────────────

    def _load_from_file(self) -> str:
        """从文件加载索引（带缓存）"""
        try:
            entrypoint = self.memory_dir / "MEMORY.md"
            if entrypoint.exists():
                current_mtime = entrypoint.stat().st_mtime
                
                # 缓存有效，直接返回
                if self._cached_index is not None and current_mtime == self._cached_mtime:
                    return self._cached_index
                
                # 缓存失效，重新加载
                self._cached_index = entrypoint.read_text(encoding="utf-8")
                self._cached_mtime = current_mtime
                return self._cached_index
            else:
                self._cached_index = ""
                self._cached_mtime = 0.0
                return ""
        except Exception as e:
            logger.warning(f"加载索引文件失败: {e}")
            self._cached_index = ""
            return ""

    def _invalidate_cache(self) -> None:
        """使缓存失效"""
        self._cached_index = None
        self._cached_mtime = 0.0
        logger.debug("索引缓存已失效")

    # ── 状态查询 ─────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取索引统计信息"""
        try:
            entrypoint = self.memory_dir / "MEMORY.md"
            file_exists = entrypoint.exists()
            file_size = entrypoint.stat().st_size if file_exists else 0
            file_mtime = entrypoint.stat().st_mtime if file_exists else 0
            
            return {
                "cached": self._cached_index is not None,
                "cache_lines": len(self._cached_index.split("\n")) if self._cached_index else 0,
                "file_exists": file_exists,
                "file_size": file_size,
                "file_mtime": file_mtime,
            }
        except Exception as e:
            return {"error": str(e)}

    def __repr__(self) -> str:
        stats = self.get_stats()
        return f"MemoryIndexer({self.memory_dir.name}, {stats.get('cache_lines', 0)}行)"


# ── 兼容性别名（已废弃）────────────────────────────────────────────

def create_indexer_node(indexer: MemoryIndexer):
    """
    创建索引更新节点（兼容旧接口）
    
    现在直接使用 indexer.rebuild() 即可
    """
    def execute():
        indexer.rebuild()
    return execute
