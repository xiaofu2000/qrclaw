"""
MemoryManager —— 增强版长期记忆管理器

参考 Claude Code 的 memdir.ts 和 memoryScan.ts 设计。

核心职责：
1. 管理 ~/.qrclaw/memory/ 目录下的记忆文件
2. 支持四种类型：user, feedback, project, reference
3. 维护 MEMORY.md 入口索引
4. 提供 CRUD 和搜索能力

目录结构：
    memory/
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

import re
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from qrclaw.memory.types import MemoryFile, MemoryIndex, MemoryType
from qrclaw.logger import get_logger

if TYPE_CHECKING:
    from qrclaw.memory.storage.indexer import MemoryIndexer

logger = get_logger("qrclaw.memory.manager")


# 限制常量（参考 Claude Code）
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000
MAX_MEMORY_FILES = 200
MAX_SECTION_LENGTH = 2000


class MemoryManager:
    """
    增强版长期记忆管理器
    
    支持：
    - 按类型分组存储
    - frontmatter 元数据
    - MEMORY.md 入口索引
    - 全文搜索
    - 与 MemoryIndexer 集成（延迟更新模式）
    """
    
    def __init__(self, memory_dir: Path):
        """
        初始化 MemoryManager
        
        Args:
            memory_dir: 记忆根目录（如 ~/.qrclaw/memory/）
        """
        self.memory_dir = memory_dir
        self._indexer: Optional["MemoryIndexer"] = None
        self._ensure_structure()
        logger.info(f"MemoryManager 初始化: {self.memory_dir}")
    
    @property
    def indexer(self) -> Optional["MemoryIndexer"]:
        """
        获取关联的 MemoryIndexer
        
        如果未设置，返回 None（单向引用，避免循环依赖）
        """
        return self._indexer
    
    @indexer.setter
    def indexer(self, value: "MemoryIndexer"):
        """设置 MemoryIndexer（通常由 LongTermMemory 注入）"""
        self._indexer = value
    
    def _ensure_structure(self):
        """确保目录结构存在"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        for memory_type in MemoryType:
            type_dir = self.memory_dir / memory_type.value
            type_dir.mkdir(parents=True, exist_ok=True)
        
        # 确保 MEMORY.md 存在
        self._ensure_entrypoint()
    
    def _ensure_entrypoint(self):
        """确保 MEMORY.md 入口文件存在"""
        entrypoint = self.memory_dir / "MEMORY.md"
        if not entrypoint.exists():
            entrypoint.write_text("# QRClaw 记忆索引\n\n", encoding="utf-8")
            logger.debug("创建 MEMORY.md 入口文件")
    
    # ── 核心 CRUD ───────────────────────────────────────────────────
    
    def save_memory(
        self,
        name: str,
        description: str,
        content: str,
        memory_type: MemoryType = MemoryType.PROJECT,
        update_existing: bool = True,
    ) -> bool:
        """
        保存记忆文件
        
        Args:
            name: 记忆名称
            description: 简短描述（用于索引）
            content: 正文内容
            memory_type: 记忆类型
            update_existing: 是否更新已存在的同名记忆
            
        Returns:
            bool: 是否成功
        """
        try:
            # 检查是否已存在
            existing = self.get_memory(name, memory_type)
            if existing and not update_existing:
                logger.info(f"记忆已存在，跳过: {name}")
                return False
            
            # 创建 MemoryFile
            memory = MemoryFile(
                name=name,
                description=description,
                type=memory_type,
                content=content,
                created_at=existing.created_at if existing else datetime.now(),
                updated_at=datetime.now(),
            )
            
            # 保存文件
            filepath = self._get_filepath(memory)
            filepath.write_text(memory.to_frontmatter(), encoding="utf-8")
            
            # 更新索引（直接更新，不触发 dirty）
            self.update_entrypoint(memory)
            
            logger.info(f"保存记忆: {name} ({memory_type.value})")
            return True
            
        except Exception as e:
            logger.error(f"保存记忆失败: {e}", exc_info=True)
            return False
    
    def get_memory(
        self,
        name: str,
        memory_type: Optional[MemoryType] = None,
    ) -> Optional[MemoryFile]:
        """
        获取单个记忆
        
        Args:
            name: 记忆名称
            memory_type: 记忆类型（可选，默认为 None 表示搜索所有类型）
            
        Returns:
            MemoryFile 或 None
        """
        if memory_type:
            filepath = self._get_filepath_by_type(name, memory_type)
            if filepath and filepath.exists():
                return self._load_memory_file(filepath)
            return None
        
        # 搜索所有类型
        for mtype in MemoryType:
            memory = self.get_memory(name, mtype)
            if memory:
                return memory
        return None
    
    def delete_memory(
        self,
        name: str,
        memory_type: Optional[MemoryType] = None,
    ) -> bool:
        """
        删除记忆
        
        Args:
            name: 记忆名称
            memory_type: 记忆类型（可选）
            
        Returns:
            bool: 是否成功
        """
        try:
            if memory_type:
                filepath = self._get_filepath_by_type(name, memory_type)
                if filepath and filepath.exists():
                    filepath.unlink()
                    self._remove_from_entrypoint(name)
                    logger.info(f"删除记忆: {name}")
                    return True
                return False
            
            # 搜索所有类型
            for mtype in MemoryType:
                if self.delete_memory(name, mtype):
                    return True
            return False
            
        except Exception as e:
            logger.error(f"删除记忆失败: {e}", exc_info=True)
            return False
    
    # ── 批量操作 ────────────────────────────────────────────────────
    
    def scan_all_memories(self) -> list[MemoryFile]:
        """
        扫描所有记忆文件
        
        Returns:
            MemoryFile 列表
        """
        memories = []
        for memory_type in MemoryType:
            type_dir = self.memory_dir / memory_type.value
            if type_dir.exists():
                for filepath in type_dir.glob("*.md"):
                    memory = self._load_memory_file(filepath)
                    if memory:
                        memories.append(memory)
        
        logger.debug(f"扫描到 {len(memories)} 个记忆文件")
        return memories
    
    def get_memories_by_type(self, memory_type: MemoryType) -> list[MemoryFile]:
        """
        按类型获取记忆
        
        Args:
            memory_type: 记忆类型
            
        Returns:
            MemoryFile 列表
        """
        memories = []
        type_dir = self.memory_dir / memory_type.value
        if type_dir.exists():
            for filepath in type_dir.glob("*.md"):
                memory = self._load_memory_file(filepath)
                if memory:
                    memories.append(memory)
        
        return memories
    
    def search_memories(self, query: str) -> list[MemoryFile]:
        """
        搜索记忆（简单的关键词匹配）
        
        Args:
            query: 搜索关键词
            
        Returns:
            匹配的记忆列表
        """
        query_lower = query.lower()
        results = []
        
        for memory in self.scan_all_memories():
            # 搜索 name, description, content
            if (query_lower in memory.name.lower() or
                query_lower in memory.description.lower() or
                query_lower in memory.content.lower()):
                results.append(memory)
        
        return results
    
    # ── 索引管理 ────────────────────────────────────────────────────
    
    def update_entrypoint(self, memory: MemoryFile) -> bool:
        """
        更新 MEMORY.md 入口索引
        
        添加或更新指定记忆的索引条目
        """
        try:
            entrypoint = self.memory_dir / "MEMORY.md"
            content = entrypoint.read_text(encoding="utf-8")
            
            index = MemoryIndex.from_memory_file(memory)
            new_line = index.to_line()
            
            # 检查是否已存在（按 name 匹配）
            pattern = rf"- \[{re.escape(memory.name)}\]\([^)]+\)"
            if re.search(pattern, content):
                # 替换已有条目
                content = re.sub(pattern, new_line, content, count=1)
            else:
                # 添加新条目
                content += new_line + "\n"
            
            # 写入（限制行数）
            lines = content.strip().split("\n")
            if len(lines) > MAX_ENTRYPOINT_LINES:
                lines = lines[:MAX_ENTRYPOINT_LINES]
                lines.append(f"\n⚠️ 记忆已超过 {MAX_ENTRYPOINT_LINES} 条，部分条目被隐藏")
            
            entrypoint.write_text("\n".join(lines) + "\n", encoding="utf-8")
            logger.debug(f"更新 MEMORY.md 索引: {memory.name}")
            return True
            
        except Exception as e:
            logger.error(f"更新索引失败: {e}", exc_info=True)
            return False
    
    def _remove_from_entrypoint(self, name: str) -> bool:
        """从 MEMORY.md 移除指定记忆的索引"""
        try:
            entrypoint = self.memory_dir / "MEMORY.md"
            content = entrypoint.read_text(encoding="utf-8")
            
            # 移除匹配行
            pattern = rf"- \[{re.escape(name)}\]\([^)]+\)[^\n]*\n?"
            content = re.sub(pattern, "", content)
            
            entrypoint.write_text(content, encoding="utf-8")
            return True
            
        except Exception as e:
            logger.error(f"移除索引失败: {e}", exc_info=True)
            return False
    
    def rebuild_entrypoint(self) -> bool:
        """
        重建 MEMORY.md 入口索引
        
        扫描所有记忆文件，重新生成完整索引
        """
        try:
            entrypoint = self.memory_dir / "MEMORY.md"
            
            lines = ["# QRClaw 记忆索引\n"]
            memories = self.scan_all_memories()
            
            # 按类型分组输出
            for memory_type in MemoryType:
                type_memories = [m for m in memories if m.type == memory_type]
                if type_memories:
                    lines.append(f"\n## {memory_type.value.capitalize()}\n")
                    for memory in type_memories:
                        index = MemoryIndex.from_memory_file(memory)
                        lines.append(index.to_line())
            
            content = "\n".join(lines) + "\n"
            
            # 限制行数
            content_lines = content.strip().split("\n")
            if len(content_lines) > MAX_ENTRYPOINT_LINES:
                content_lines = content_lines[:MAX_ENTRYPOINT_LINES]
                content_lines.append(f"\n⚠️ 记忆已超过 {MAX_ENTRYPOINT_LINES} 条，部分条目被隐藏")
            
            entrypoint.write_text("\n".join(content_lines) + "\n", encoding="utf-8")
            logger.info(f"重建 MEMORY.md 索引，共 {len(memories)} 条记忆")
            return True
            
        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            return False
    
    # ── 辅助方法 ────────────────────────────────────────────────────
    
    def _get_filepath(self, memory: MemoryFile) -> Path:
        """获取记忆文件的完整路径"""
        return self.memory_dir / memory.type_path / memory.filename
    
    def _get_filepath_by_type(self, name: str, memory_type: MemoryType) -> Path:
        """按类型获取记忆文件路径"""
        slug = MemoryFile._slugify(name)
        return self.memory_dir / memory_type.value / f"{slug}.md"
    
    def _load_memory_file(self, filepath: Path) -> Optional[MemoryFile]:
        """加载单个记忆文件"""
        try:
            content = filepath.read_text(encoding="utf-8")
            return MemoryFile.from_frontmatter(str(filepath), content)
        except Exception as e:
            logger.warning(f"加载记忆文件失败: {filepath}, {e}")
            return None
    
    # ── 兼容性方法 ───────────────────────────────────────────────────
    
    def load(self) -> str:
        """
        加载 MEMORY.md 内容（兼容原有 LongTermMemory 接口）
        """
        entrypoint = self.memory_dir / "MEMORY.md"
        if entrypoint.exists():
            return entrypoint.read_text(encoding="utf-8")
        return ""
    
    def append(self, content: str, title: str = None) -> bool:
        """
        追加到默认记忆文件（兼容原有 LongTermMemory 接口）
        
        默认保存到 project 类型
        """
        return self.save_memory(
            name=title or f"记忆_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            description=title or "无标题",
            content=content,
            memory_type=MemoryType.PROJECT,
        )
    
    def clear(self) -> bool:
        """
        清空所有记忆（兼容原有 LongTermMemory 接口）
        """
        try:
            for memory_type in MemoryType:
                type_dir = self.memory_dir / memory_type.value
                if type_dir.exists():
                    for filepath in type_dir.glob("*.md"):
                        filepath.unlink()
            
            self._ensure_entrypoint()
            logger.info("清空所有记忆")
            return True
        except Exception as e:
            logger.error(f"清空记忆失败: {e}", exc_info=True)
            return False
