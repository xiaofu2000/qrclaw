"""
记忆类型定义模块

包含：
- MemoryType: 四种记忆类型枚举
- MemoryFile: 单个记忆文件数据结构
- MemoryIndex: MEMORY.md 索引条目
- Frontmatter 相关工具函数
"""

from qrclaw.memory.types.types import (
    MemoryType,
    MemoryFile,
    MemoryIndex,
)

from qrclaw.memory.types.frontmatter import (
    Frontmatter,
    FrontmatterParser,
    FrontmatterBuilder,
    parse_frontmatter,
    build_frontmatter,
    extract_frontmatter_fields,
    update_frontmatter_timestamp,
    strip_frontmatter,
    has_frontmatter,
)

__all__ = [
    # 类型定义
    "MemoryType",
    "MemoryFile",
    "MemoryIndex",
    
    # Frontmatter 工具
    "Frontmatter",
    "FrontmatterParser",
    "FrontmatterBuilder",
    "parse_frontmatter",
    "build_frontmatter",
    "extract_frontmatter_fields",
    "update_frontmatter_timestamp",
    "strip_frontmatter",
    "has_frontmatter",
]
