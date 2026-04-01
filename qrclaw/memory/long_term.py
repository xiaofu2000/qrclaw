"""
长期记忆兼容层

将 LongTermMemory 保留在根目录以保持向后兼容。
实际的实现现在位于 core/long_term.py
"""

from qrclaw.memory.core.long_term import LongTermMemory

__all__ = ["LongTermMemory"]
