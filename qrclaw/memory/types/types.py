"""
记忆类型定义

定义记忆文件的结构和类型常量。
参考 Claude Code 的 memoryTypes.ts
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.types")


class MemoryType(str, Enum):
    """四种记忆类型，与 Claude Code 完全一致"""
    USER = "user"           # 用户角色、目标、知识（私有）
    FEEDBACK = "feedback"   # 行为指导（私有/团队）
    PROJECT = "project"     # 项目上下文（团队倾向）
    REFERENCE = "reference"  # 外部系统指针（通常团队）

    @classmethod
    def from_str(cls, value: str) -> "MemoryType":
        """从字符串解析记忆类型"""
        try:
            return cls(value.lower())
        except ValueError:
            logger.warning(f"未知的记忆类型 '{value}'，默认为 project")
            return cls.PROJECT


@dataclass
class MemoryFile:
    """
    单个记忆文件的数据结构
    
    属性参考 Claude Code 的 MemoryFile 接口：
    - name: 记忆名称（用于生成文件名）
    - description: 简短描述（用于 MEMORY.md 索引）
    - type: 记忆类型
    - content: 正文内容
    - createdAt: 创建时间
    - updatedAt: 更新时间
    """
    name: str
    description: str
    type: MemoryType
    content: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    @property
    def filename(self) -> str:
        """生成文件名：slugify(name) + .md"""
        return self._slugify(self.name) + ".md"
    
    @property
    def type_path(self) -> str:
        """获取类型子目录名"""
        return self.type.value
    
    def to_frontmatter(self) -> str:
        """
        生成带 frontmatter 的 Markdown 内容
        
        格式参考 Claude Code：
        ```yaml
        ---
        name: 用户角色
        description: 数据科学家，专注日志
        type: user
        ---
        ```
        """
        lines = [
            "---",
            f"name: {self.name}",
            f"description: {self.description}",
            f"type: {self.type.value}",
            f"created_at: {self.created_at.isoformat()}",
            f"updated_at: {self.updated_at.isoformat()}",
            "---",
            "",
            self.content,
        ]
        return "\n".join(lines)
    
    @classmethod
    def from_frontmatter(cls, filepath: str, text: str) -> Optional["MemoryFile"]:
        """
        从带 frontmatter 的文本解析 MemoryFile
        
        Args:
            filepath: 文件路径（用于提取 name）
            text: 文件完整内容
            
        Returns:
            MemoryFile 或 None（解析失败时）
        """
        import re
        
        # 解析 frontmatter
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
        if not match:
            logger.warning(f"无法解析 frontmatter: {filepath}")
            # 尝试无 frontmatter 解析
            return cls._parse_without_frontmatter(filepath, text)
        
        frontmatter_text = match.group(1)
        content = match.group(2).strip()
        
        # 解析 frontmatter 字段
        fields = {}
        for line in frontmatter_text.split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
        
        try:
            memory_type = MemoryType.from_str(fields.get("type", "project"))
            created_at = datetime.fromisoformat(fields["created_at"]) if "created_at" in fields else datetime.now()
            updated_at = datetime.fromisoformat(fields["updated_at"]) if "updated_at" in fields else datetime.now()
            
            return cls(
                name=fields.get("name", cls._extract_name_from_path(filepath)),
                description=fields.get("description", ""),
                type=memory_type,
                content=content,
                created_at=created_at,
                updated_at=updated_at,
            )
        except Exception as e:
            logger.error(f"解析 MemoryFile 失败: {e}", exc_info=True)
            return cls._parse_without_frontmatter(filepath, text)
    
    @classmethod
    def _parse_without_frontmatter(cls, filepath: str, text: str) -> Optional["MemoryFile"]:
        """无 frontmatter 时，尝试从文件名和内容解析"""
        import re
        
        name = cls._extract_name_from_path(filepath)
        # 取第一行作为描述（去掉 # 标题）
        first_line = text.split("\n")[0] if text else ""
        description = re.sub(r"^#+\s*", "", first_line).strip()
        
        return cls(
            name=name,
            description=description[:100] if description else "无描述",
            type=MemoryType.PROJECT,
            content=text,
        )
    
    @classmethod
    def _extract_name_from_path(cls, filepath: str) -> str:
        """从文件路径提取名称"""
        import os
        basename = os.path.basename(filepath)
        name = os.path.splitext(basename)[0]
        # 替换下划线和连字符为空格
        name = name.replace("_", " ").replace("-", " ")
        return name
    
    @staticmethod
    def _slugify(text: str) -> str:
        """将文本转换为 URL-safe 的 slug"""
        import re
        # 转小写，替换空格为下划线
        slug = text.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[-\s]+", "_", slug)
        return slug


@dataclass
class MemoryIndex:
    """
    MEMORY.md 索引条目
    
    Claude Code 的 MEMORY.md 格式：
    ```markdown
    - [用户角色](user_role.md) — 数据科学家，专注日志
    - [测试反馈](feedback_testing.md) — 不用 mock
    ```
    """
    name: str
    filename: str
    description: str
    type: MemoryType
    
    def to_line(self) -> str:
        """生成索引行，包含类型子目录路径"""
        relative_path = f"{self.type.value}/{self.filename}"
        return f"- [{self.name}]({relative_path}) — {self.description}"
    
    @classmethod
    def from_memory_file(cls, memory: MemoryFile) -> "MemoryIndex":
        """从 MemoryFile 创建索引条目"""
        return cls(
            name=memory.name,
            filename=memory.filename,
            description=memory.description,
            type=memory.type,
        )
