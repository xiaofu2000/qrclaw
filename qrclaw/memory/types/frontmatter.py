"""
Frontmatter 工具模块

提供 frontmatter 的解析和生成功能，供 MemoryIndexer 调用。
基于 Claude Code 的 frontmatter 格式：

    ---
    name: 用户角色
    description: 数据科学家，专注日志分析
    type: user
    created_at: 2026-03-27T00:00:00
    updated_at: 2026-03-27T00:00:00
    ---

模块职责：
1. FrontmatterParser - 解析 frontmatter 和正文
2. FrontmatterBuilder - 从数据构建 frontmatter
3. frontmatter_utils - 独立工具函数
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

from qrclaw.memory.types import MemoryType
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.frontmatter")


# =============================================================================
# Frontmatter 数据结构
# =============================================================================

@dataclass
class Frontmatter:
    """
    Frontmatter 数据结构
    
    对应 Claude Code 的 MemoryFile frontmatter 部分：
    - name: 记忆名称（用于生成文件名）
    - description: 简短描述（用于索引显示）
    - type: 记忆类型
    - created_at: 创建时间（ISO 8601）
    - updated_at: 更新时间（ISO 8601）
    - extra: 额外的自定义字段
    """
    name: str
    description: str
    type: MemoryType
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    extra: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_valid(self) -> bool:
        """检查 frontmatter 是否有效"""
        return bool(self.name and self.type)


# =============================================================================
# Frontmatter 解析器
# =============================================================================

class FrontmatterParser:
    """
    Frontmatter 解析器
    
    将 Markdown 文本拆分为 frontmatter 元数据和正文内容。
    
    使用示例：
        parser = FrontmatterParser()
        fm, content = parser.parse(text)
        if fm:
            print(f"类型: {fm.type.value}, 名称: {fm.name}")
            print(f"正文: {content[:100]}...")
    """
    
    # 匹配 frontmatter 分隔符之间的内容
    FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
    
    # 字段解析正则
    FIELD_PATTERN = re.compile(r"^(\w+):\s*(.*)$", re.MULTILINE)
    
    @classmethod
    def parse(cls, text: str) -> Tuple[Optional[Frontmatter], str]:
        """
        解析 Markdown 文本中的 frontmatter
        
        Args:
            text: 完整的 Markdown 文本（可能包含 frontmatter）
            
        Returns:
            (Frontmatter, 正文内容) 元组
            如果没有 frontmatter，返回 (None, 原文本)
            
        Examples:
            >>> text = "---\\nname: test\\ntype: user\\n---\\n\\n正文内容"
            >>> fm, content = FrontmatterParser.parse(text)
            >>> fm.name
            'test'
            >>> content
            '正文内容'
        """
        if not text or not text.strip():
            return None, text
        
        match = cls.FRONTMATTER_PATTERN.match(text.strip())
        if not match:
            logger.debug("未找到 frontmatter 分隔符")
            return None, text
        
        frontmatter_text = match.group(1)
        content = text[match.end():].lstrip("\n")
        
        try:
            fm = cls._parse_frontmatter_text(frontmatter_text)
            return fm, content
        except Exception as e:
            logger.error(f"解析 frontmatter 失败: {e}", exc_info=True)
            return None, text
    
    @classmethod
    def _parse_frontmatter_text(cls, text: str) -> Frontmatter:
        """
        解析 frontmatter 文本内容
        
        Args:
            text: --- 和 --- 之间的文本
            
        Returns:
            Frontmatter 对象
        """
        fields = {}
        extra = {}
        
        for match in cls.FIELD_PATTERN.finditer(text):
            key = match.group(1)
            value = match.group(2).strip()
            
            if key in ("name", "description"):
                fields[key] = value
            elif key == "type":
                fields[key] = MemoryType.from_str(value)
            elif key in ("created_at", "updated_at"):
                fields[key] = cls._parse_datetime(value)
            else:
                # 收集额外字段
                extra[key] = cls._parse_value(value)
        
        # 确保必需字段
        if "name" not in fields:
            raise ValueError("frontmatter 缺少 'name' 字段")
        if "type" not in fields:
            fields["type"] = MemoryType.PROJECT
        
        # 设置默认值
        now = datetime.now()
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
        
        return Frontmatter(
            name=fields["name"],
            description=fields.get("description", ""),
            type=fields["type"],
            created_at=fields["created_at"],
            updated_at=fields["updated_at"],
            extra=extra,
        )
    
    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        """解析 ISO 8601 日期时间"""
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            # 尝试其他常见格式
            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d",
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
            logger.warning(f"无法解析日期时间 '{value}'，使用当前时间")
            return datetime.now()
    
    @staticmethod
    def _parse_value(value: str) -> Any:
        """解析 YAML 风格的简单值"""
        # 布尔值
        if value.lower() in ("true", "yes", "on"):
            return True
        if value.lower() in ("false", "no", "off"):
            return False
        
        # 数字
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            pass
        
        # 字符串（去除引号）
        if len(value) >= 2:
            if (value[0] == '"' and value[-1] == '"') or \
               (value[0] == "'" and value[-1] == "'"):
                return value[1:-1]
        
        return value


# =============================================================================
# Frontmatter 生成器
# =============================================================================

class FrontmatterBuilder:
    """
    Frontmatter 生成器
    
    将 Frontmatter 对象转换为 Markdown frontmatter 文本。
    
    使用示例：
        fm = Frontmatter(
            name="用户角色",
            description="数据科学家",
            type=MemoryType.USER,
        )
        yaml_text = FrontmatterBuilder.build(fm)
        full_text = FrontmatterBuilder.assemble(fm, "正文内容")
    """
    
    # 标准字段顺序
    STANDARD_FIELDS = ["name", "description", "type", "created_at", "updated_at"]
    
    @classmethod
    def build(cls, fm: Frontmatter) -> str:
        """
        构建 frontmatter 文本
        
        Args:
            fm: Frontmatter 对象
            
        Returns:
            YAML 格式的 frontmatter 文本（包含 --- 分隔符）
        """
        lines = ["---"]
        
        # 添加标准字段
        lines.append(f"name: {fm.name}")
        lines.append(f"description: {fm.description}")
        lines.append(f"type: {fm.type.value}")
        lines.append(f"created_at: {fm.created_at.isoformat()}")
        lines.append(f"updated_at: {fm.updated_at.isoformat()}")
        
        # 添加额外字段
        for key, value in fm.extra.items():
            lines.append(f"{key}: {cls._format_value(value)}")
        
        lines.append("---")
        
        return "\n".join(lines)
    
    @classmethod
    def assemble(cls, fm: Frontmatter, content: str, add_newline: bool = True) -> str:
        """
        组装完整的 Markdown 文本
        
        Args:
            fm: Frontmatter 对象
            content: 正文内容
            add_newline: 是否在 frontmatter 后添加空行
            
        Returns:
            完整的 Markdown 文本
        """
        frontmatter_text = cls.build(fm)
        
        if add_newline:
            return f"{frontmatter_text}\n\n{content}"
        else:
            return f"{frontmatter_text}\n{content}"
    
    @staticmethod
    def _format_value(value: Any) -> str:
        """格式化值为 YAML 字符串"""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            # 如果包含特殊字符，需要加引号
            if any(c in value for c in [":", "#", "\n", "'", '"', "[", "]"]):
                # 转义内部引号
                value = value.replace('"', '\\"')
                return f'"{value}"'
            return value
        return str(value)


# =============================================================================
# 便捷工具函数
# =============================================================================

def parse_frontmatter(text: str) -> Tuple[Optional[Frontmatter], str]:
    """
    解析 frontmatter 的便捷函数
    
    Args:
        text: Markdown 文本
        
    Returns:
        (Frontmatter, 正文) 元组
    """
    return FrontmatterParser.parse(text)


def build_frontmatter(
    name: str,
    description: str,
    type: MemoryType,
    created_at: Optional[datetime] = None,
    updated_at: Optional[datetime] = None,
    **extra: Any,
) -> str:
    """
    构建 frontmatter 的便捷函数
    
    Args:
        name: 记忆名称
        description: 简短描述
        type: 记忆类型
        created_at: 创建时间（默认当前时间）
        updated_at: 更新时间（默认当前时间）
        **extra: 额外字段
        
    Returns:
        YAML 格式的 frontmatter 文本
    """
    fm = Frontmatter(
        name=name,
        description=description,
        type=type,
        created_at=created_at or datetime.now(),
        updated_at=updated_at or datetime.now(),
        extra=extra,
    )
    return FrontmatterBuilder.build(fm)


def extract_frontmatter_fields(text: str) -> Dict[str, Any]:
    """
    从文本中提取 frontmatter 字段（不返回正文）
    
    适合只想检查 frontmatter 存在的场景。
    
    Args:
        text: Markdown 文本
        
    Returns:
        字段字典，缺少的字段返回 None
    """
    fm, _ = FrontmatterParser.parse(text)
    if not fm:
        return {}
    
    result = {
        "name": fm.name,
        "description": fm.description,
        "type": fm.type.value,
        "created_at": fm.created_at,
        "updated_at": fm.updated_at,
    }
    result.update(fm.extra)
    return result


def update_frontmatter_timestamp(text: str, updated_at: Optional[datetime] = None) -> str:
    """
    更新 frontmatter 的 updated_at 时间戳
    
    Args:
        text: 原始 Markdown 文本
        updated_at: 新的时间戳（默认当前时间）
        
    Returns:
        更新后的 Markdown 文本（如果原来没有 frontmatter 则原样返回）
    """
    fm, content = FrontmatterParser.parse(text)
    if not fm:
        return text
    
    fm.updated_at = updated_at or datetime.now()
    return FrontmatterBuilder.assemble(fm, content)


def strip_frontmatter(text: str) -> str:
    """
    移除文本中的 frontmatter，只保留正文
    
    Args:
        text: 完整的 Markdown 文本
        
    Returns:
        移除 frontmatter 后的正文内容
    """
    _, content = FrontmatterParser.parse(text)
    return content


def has_frontmatter(text: str) -> bool:
    """
    检查文本是否包含 frontmatter
    
    Args:
        text: Markdown 文本
        
    Returns:
        True 如果包含 frontmatter
    """
    return bool(FrontmatterParser.FRONTMATTER_PATTERN.match(text.strip()))


# =============================================================================
# 模块导出
# =============================================================================

__all__ = [
    # 数据结构
    "Frontmatter",
    # 解析器
    "FrontmatterParser",
    # 生成器
    "FrontmatterBuilder",
    # 便捷函数
    "parse_frontmatter",
    "build_frontmatter",
    "extract_frontmatter_fields",
    "update_frontmatter_timestamp",
    "strip_frontmatter",
    "has_frontmatter",
]
