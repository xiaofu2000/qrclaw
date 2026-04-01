"""
记忆文件存储模块

负责记忆文件的读写、扫描、搜索等底层操作
"""

from pathlib import Path
from datetime import datetime
from typing import Optional

from qrclaw.logger import get_logger
from qrclaw.memory.types import MemoryFile, MemoryType

logger = get_logger("qrclaw.memory.storage")


class MemoryStorage:
    """记忆文件存储管理器"""

    def __init__(self, memory_dir: Path):
        """
        初始化存储管理器

        Args:
            memory_dir: 记忆根目录 (e.g., ~/.qrclaw/memory/)
        """
        self.memory_dir = Path(memory_dir)
        self._ensure_dirs()
        logger.info(f"记忆存储初始化: {self.memory_dir}")

    def _ensure_dirs(self):
        """确保所有必要目录存在"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        for memory_type in MemoryType:
            type_dir = self.memory_dir / memory_type.value
            type_dir.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, memory: MemoryFile) -> Path:
        """获取记忆文件的完整路径"""
        return self.memory_dir / memory.type_path / memory.filename

    def save(self, memory: MemoryFile) -> bool:
        """
        保存记忆文件

        Args:
            memory: 记忆文件对象

        Returns:
            bool: 是否成功
        """
        try:
            file_path = self._get_file_path(memory)
            content = memory.to_frontmatter()
            file_path.write_text(content, encoding="utf-8")
            logger.info(f"保存记忆: {memory.name} -> {file_path}")
            return True
        except Exception as e:
            logger.error(f"保存记忆失败: {memory.name}, {e}", exc_info=True)
            return False

    def load(self, memory: MemoryFile) -> Optional[MemoryFile]:
        """
        从文件加载记忆

        Args:
            memory: 记忆文件对象（包含 name 和 type）

        Returns:
            MemoryFile 或 None
        """
        try:
            file_path = self._get_file_path(memory)
            if not file_path.exists():
                logger.warning(f"记忆文件不存在: {file_path}")
                return None

            content = file_path.read_text(encoding="utf-8")
            loaded = MemoryFile.from_frontmatter(str(file_path), content)
            if loaded:
                logger.debug(f"加载记忆: {memory.name}")
            return loaded
        except Exception as e:
            logger.error(f"加载记忆失败: {memory.name}, {e}", exc_info=True)
            return None

    def load_by_path(self, file_path: Path) -> Optional[MemoryFile]:
        """
        从指定路径加载记忆

        Args:
            file_path: 完整文件路径

        Returns:
            MemoryFile 或 None
        """
        try:
            if not file_path.exists():
                return None

            content = file_path.read_text(encoding="utf-8")
            memory = MemoryFile.from_frontmatter(str(file_path), content)
            return memory
        except Exception as e:
            logger.error(f"从路径加载记忆失败: {file_path}, {e}", exc_info=True)
            return None

    def delete(self, memory: MemoryFile) -> bool:
        """
        删除记忆文件

        Args:
            memory: 记忆文件对象

        Returns:
            bool: 是否成功
        """
        try:
            file_path = self._get_file_path(memory)
            if file_path.exists():
                file_path.unlink()
                logger.info(f"删除记忆: {memory.name}")
            return True
        except Exception as e:
            logger.error(f"删除记忆失败: {memory.name}, {e}", exc_info=True)
            return False

    def delete_by_name(self, name: str, memory_type: Optional[MemoryType] = None) -> bool:
        """
        按名称删除记忆

        Args:
            name: 记忆名称
            memory_type: 可选的类型限定

        Returns:
            bool: 是否成功
        """
        if memory_type:
            # 只在指定类型目录搜索
            type_dir = self.memory_dir / memory_type.value
            for file_path in type_dir.glob("*.md"):
                memory = self.load_by_path(file_path)
                if memory and memory.name == name:
                    return self.delete(memory)
        else:
            # 全局搜索
            for file_path in self.memory_dir.rglob("*.md"):
                memory = self.load_by_path(file_path)
                if memory and memory.name == name:
                    return self.delete(memory)

        logger.warning(f"未找到记忆: {name}")
        return False

    def scan_all(self) -> list[MemoryFile]:
        """
        扫描所有记忆文件

        Returns:
            MemoryFile 列表
        """
        memories = []
        for file_path in self.memory_dir.rglob("*.md"):
            # 跳过入口文件
            if file_path.name == "MEMORY.md":
                continue

            memory = self.load_by_path(file_path)
            if memory:
                memories.append(memory)

        logger.debug(f"扫描到 {len(memories)} 个记忆文件")
        return memories

    def scan_by_type(self, memory_type: MemoryType) -> list[MemoryFile]:
        """
        按类型扫描记忆

        Args:
            memory_type: 记忆类型

        Returns:
            MemoryFile 列表
        """
        memories = []
        type_dir = self.memory_dir / memory_type.value

        if not type_dir.exists():
            return memories

        for file_path in type_dir.glob("*.md"):
            memory = self.load_by_path(file_path)
            if memory:
                memories.append(memory)

        return memories

    def search(self, query: str) -> list[MemoryFile]:
        """
        搜索记忆（基于名称和描述）

        Args:
            query: 搜索关键词

        Returns:
            匹配的记忆列表
        """
        query_lower = query.lower()
        results = []

        for memory in self.scan_all():
            if (query_lower in memory.name.lower() or
                query_lower in memory.description.lower() or
                query_lower in memory.content.lower()):
                results.append(memory)

        return results

    def exists(self, name: str, memory_type: Optional[MemoryType] = None) -> bool:
        """
        检查记忆是否存在

        Args:
            name: 记忆名称
            memory_type: 可选的类型限定

        Returns:
            bool
        """
        if memory_type:
            type_dir = self.memory_dir / memory_type.value
            for file_path in type_dir.glob("*.md"):
                memory = self.load_by_path(file_path)
                if memory and memory.name == name:
                    return True
        else:
            for file_path in self.memory_dir.rglob("*.md"):
                memory = self.load_by_path(file_path)
                if memory and memory.name == name:
                    return True

        return False

    def get(self, name: str, memory_type: Optional[MemoryType] = None) -> Optional[MemoryFile]:
        """
        按名称获取记忆

        Args:
            name: 记忆名称
            memory_type: 可选的类型限定

        Returns:
            MemoryFile 或 None
        """
        if memory_type:
            type_dir = self.memory_dir / memory_type.value
            for file_path in type_dir.glob("*.md"):
                memory = self.load_by_path(file_path)
                if memory and memory.name == name:
                    return memory
        else:
            for file_path in self.memory_dir.rglob("*.md"):
                memory = self.load_by_path(file_path)
                if memory and memory.name == name:
                    return memory

        return None

    def count(self) -> int:
        """获取记忆总数"""
        return len(self.scan_all())

    def count_by_type(self) -> dict[MemoryType, int]:
        """获取各类别的记忆数量"""
        counts = {}
        for memory_type in MemoryType:
            counts[memory_type] = len(self.scan_by_type(memory_type))
        return counts

    def get_memory_dir(self) -> Path:
        """获取记忆根目录"""
        return self.memory_dir
