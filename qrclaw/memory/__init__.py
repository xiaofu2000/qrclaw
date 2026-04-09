"""
QRClaw 记忆系统模块

提供多层次的记忆管理能力：
- Session: 短期会话记忆
- LongTermMemory: 中期持久化记忆
- MemoryManager: 完整的记忆管理系统（参考 Claude Code 设计）

目录结构：
- core/: 核心管理器
- storage/: 文件存储与索引
- context/: 上下文管理
- types/: 类型定义
- compression/: 压缩工具
"""

# 核心组件
from qrclaw.memory.core import MemoryManager, LongTermMemory

# 存储层
from qrclaw.memory.storage import MemoryIndexer

# 上下文管理
from qrclaw.memory.context.session import Session
from qrclaw.memory.context.step_result import StepResult

# 类型定义
from qrclaw.memory.types import (
    MemoryType,
    MemoryFile,
    MemoryIndex,
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

# 压缩工具
from qrclaw.memory.compression import (
    count_tokens,
    count_text_tokens,
    summarize,
    truncate,
)

# ContextManager 延迟导入（依赖 qrclaw.prompt，可能导致循环导入）
ContextManager = None
try:
    from qrclaw.memory.context.context_manager import ContextManager
except ImportError:
    pass

__all__ = [
    # 核心组件
    "MemoryManager",
    "LongTermMemory",

    # 存储层
    "MemoryIndexer",

    # 上下文管理
    "Session",
    "ContextManager",
    "StepResult",

    # 类型定义
    "MemoryType",
    "MemoryFile",
    "MemoryIndex",
    "Frontmatter",
    "FrontmatterParser",
    "FrontmatterBuilder",
    "parse_frontmatter",
    "build_frontmatter",
    "extract_frontmatter_fields",
    "update_frontmatter_timestamp",
    "strip_frontmatter",
    "has_frontmatter",

    # 压缩工具
    "count_tokens",
    "count_text_tokens",
    "summarize",
    "truncate",
]
