"""
记忆压缩模块

提供上下文压缩功能，包括：
- count_tokens: 精确计算 token 数
- summarize: 递归摘要压缩
- truncate: 滚动截断
"""

from qrclaw.memory.compression.compressor import (
    count_tokens,
    count_text_tokens,
    summarize,
    truncate,
)

# 兼容旧代码的别名
compressor = None  # 保持向后兼容

__all__ = [
    "count_tokens",
    "count_text_tokens",
    "summarize",
    "truncate",
]
