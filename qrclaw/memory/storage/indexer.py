"""
MemoryIndexer —— 延迟更新索引管理器

参考 Claude Code 的 memoryScan.ts 设计。

核心职责：
1. 管理 MEMORY.md 索引的"脏"状态
2. 实现延迟更新：mark_dirty() 标记脏，get_index() 按需重建
3. 提供 Graph默默执行节点调用的接口

设计模式：
- Dirty Flag 模式：避免每次写入都重建索引
- Lazy Rebuild：在 get_index() 时检查 dirty 标志，需要时重建
- 独立索引缓存：避免重复读取文件
"""

import os
from pathlib import Path
from typing import Optional
from threading import Lock
from qrclaw.memory.core.memory_manager import MemoryManager
from qrclaw.memory.types import MemoryFile
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.indexer")


class MemoryIndexer:
    """
    延迟更新索引管理器
    
    解决的问题：
    - 每次 save_memory 都重建索引 → 性能差
    - 需要批量操作后再统一更新 → Dirty Flag 模式
    
    使用方式：
    ```python
    indexer = MemoryIndexer(memory_dir)
    
    # 标记需要重建（由 save/delete 操作调用）
    indexer.mark_dirty()
    
    # 获取索引（如果 dirty 会自动重建）
    index = indexer.get_index()
    
    # 手动重建
    indexer.rebuild()
    ```
    """

    def __init__(self, memory_dir: Path):
        """
        初始化 MemoryIndexer
        
        Args:
            memory_dir: 记忆根目录（~/.qrclaw/memory/）
        """
        self.memory_dir = memory_dir
        self.memory_manager = MemoryManager(memory_dir)
        
        # 脏标记
        self._dirty = False
        self._lock = Lock()
        
        # 索引缓存
        self._cached_index: Optional[str] = None
        self._cached_mtime: float = 0.0
        
        logger.debug(f"MemoryIndexer 初始化: {memory_dir}")

    # ── 脏标记管理 ──────────────────────────────────────────────────

    def mark_dirty(self, reason: str = "") -> None:
        """
        标记索引为脏，需要重建
        
        Args:
            reason: 脏的原因（用于日志）
        """
        with self._lock:
            if not self._dirty:
                self._dirty = True
                self._cached_index = None  # 清空缓存
                logger.debug(f"索引标记为脏: {reason}" if reason else "索引标记为脏")

    def is_dirty(self) -> bool:
        """检查索引是否脏"""
        with self._lock:
            return self._dirty

    def clear_dirty(self) -> None:
        """清除脏标记"""
        with self._lock:
            self._dirty = False
            logger.debug("脏标记已清除")

    # ── 索引获取 ─────────────────────────────────────────────────────

    def get_index(self, force_rebuild: bool = False) -> str:
        """
        获取 MEMORY.md 索引内容
        
        策略：
        - 如果 dirty 或 force_rebuild → 自动重建
        - 否则返回缓存（如果存在）
        
        Args:
            force_rebuild: 是否强制重建
            
        Returns:
            MEMORY.md 的内容字符串
        """
        with self._lock:
            if force_rebuild or self._dirty:
                self._rebuild_unlocked()
            
            if self._cached_index is None:
                # 缓存不存在，读取文件
                self._load_from_file_unlocked()
            
            return self._cached_index or ""

    def get_index_lines(self, force_rebuild: bool = False) -> list[str]:
        """
        获取索引行列表
        
        Args:
            force_rebuild: 是否强制重建
            
        Returns:
            按行分割的索引内容
        """
        content = self.get_index(force_rebuild=force_rebuild)
        return content.strip().split("\n") if content else []

    # ── 重建逻辑 ─────────────────────────────────────────────────────

    def rebuild(self) -> bool:
        """
        手动触发索引重建
        
        Returns:
            bool: 是否成功
        """
        with self._lock:
            return self._rebuild_unlocked()

    def _rebuild_unlocked(self) -> bool:
        """
        内部重建方法（需要持有锁）
        
        Returns:
            bool: 是否成功
        """
        try:
            # 调用 MemoryManager 的扫描 + 重建
            success = self.memory_manager.rebuild_entrypoint()
            
            if success:
                # 更新缓存
                self._load_from_file_unlocked()
                self._dirty = False
                logger.info("索引重建完成")
            else:
                logger.warning("索引重建失败")
            
            return success
            
        except Exception as e:
            logger.error(f"索引重建异常: {e}", exc_info=True)
            return False

    def _load_from_file_unlocked(self) -> None:
        """从文件加载索引到缓存（需要持有锁）"""
        try:
            entrypoint = self.memory_dir / "MEMORY.md"
            if entrypoint.exists():
                self._cached_index = entrypoint.read_text(encoding="utf-8")
                self._cached_mtime = entrypoint.stat().st_mtime
            else:
                self._cached_index = ""
                self._cached_mtime = 0.0
        except Exception as e:
            logger.warning(f"加载索引文件失败: {e}")
            self._cached_index = ""

    # ── 增量更新 ─────────────────────────────────────────────────────

    def update_single_entry(self, memory: MemoryFile) -> bool:
        """
        更新单条索引条目（增量更新，不触发全量重建）
        
        比 rebuild() 更高效，适用于已知具体记忆的场景。
        
        Args:
            memory: MemoryFile 实例
            
        Returns:
            bool: 是否成功
        """
        try:
            success = self.memory_manager.update_entrypoint(memory)
            if success:
                # 使缓存失效，下次 get_index 会重新加载
                self._cached_index = None
                self._cached_mtime = 0.0
                self.clear_dirty()
            return success
        except Exception as e:
            logger.error(f"更新单条索引失败: {e}", exc_info=True)
            return False

    def remove_single_entry(self, name: str) -> bool:
        """
        移除单条索引条目（增量更新）
        
        Args:
            name: 记忆名称
            
        Returns:
            bool: 是否成功
        """
        try:
            success = self.memory_manager._remove_from_entrypoint(name)
            if success:
                self._cached_index = None
                self._cached_mtime = 0.0
            return success
        except Exception as e:
            logger.error(f"移除索引条目失败: {e}", exc_info=True)
            return False

    # ── 外部文件监控 ─────────────────────────────────────────────────

    def check_external_changes(self) -> bool:
        """
        检查外部是否修改了 MEMORY.md
        
        用于 Graph 节点在执行前检查是否需要更新缓存。
        
        Returns:
            bool: 是否有外部修改
        """
        try:
            entrypoint = self.memory_dir / "MEMORY.md"
            if entrypoint.exists():
                current_mtime = entrypoint.stat().st_mtime
                if current_mtime > self._cached_mtime:
                    # 外部已修改，同步缓存
                    self._load_from_file_unlocked()
                    self.clear_dirty()
                    logger.debug("检测到外部修改，已同步缓存")
                    return True
            return False
        except Exception:
            return False

    # ── 状态查询 ─────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """
        获取索引统计信息
        
        Returns:
            dict: 包含 dirty 状态、缓存状态、文件信息等
        """
        try:
            entrypoint = self.memory_dir / "MEMORY.md"
            file_exists = entrypoint.exists()
            file_size = entrypoint.stat().st_size if file_exists else 0
            file_mtime = entrypoint.stat().st_mtime if file_exists else 0
            
            return {
                "dirty": self.is_dirty(),
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
        status = "脏" if stats.get("dirty") else "干净"
        return f"MemoryIndexer({self.memory_dir.name}, {status}, {stats.get('cache_lines', 0)}行)"


# ── Graph 执行节点工厂函数 ─────────────────────────────────────────

def create_indexer_node(indexer: MemoryIndexer):
    """
    创建 Graph默默执行的索引节点
    
    这是一个可调用对象，Graph 可以在合适的时机调用它来更新索引。
    
    使用方式：
    ```python
    indexer_node = create_indexer_node(my_indexer)
    
    # Graph 在合适时机调用
    if indexer.is_dirty():
        indexer_node()  # 默默执行，不返回结果
    ```
    
    Returns:
        callable: 无参数、无返回值的节点函数
    """
    def execute():
        """默默执行索引更新"""
        if indexer.is_dirty():
            indexer.rebuild()
    
    return execute
