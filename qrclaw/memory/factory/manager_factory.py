"""
记忆管理器工厂模块

提供便捷的 MemoryManager 创建函数
"""

from pathlib import Path
from qrclaw.memory.core.memory_manager import MemoryManager


def get_default_memory_dir() -> Path:
    """
    获取默认记忆目录

    Returns:
        Path: ~/.qrclaw/memory/
    """
    home = Path.home()
    return home / ".qrclaw" / "memory"


def create_memory_manager(
    agent_id: str = None,
    memory_dir: Path = None,
) -> MemoryManager:
    """
    创建记忆管理器

    Args:
        agent_id: Agent ID（用于隔离不同 agent 的记忆）
        memory_dir: 自定义记忆目录

    Returns:
        MemoryManager 实例
    """
    if memory_dir:
        base_dir = Path(memory_dir)
    elif agent_id:
        # 按 agent 隔离记忆
        base_dir = Path.home() / ".qrclaw" / "agents" / agent_id / "memory"
    else:
        # 使用默认目录
        base_dir = get_default_memory_dir()

    return MemoryManager(memory_dir=base_dir)


__all__ = [
    "MemoryManager",
    "create_memory_manager",
    "get_default_memory_dir",
]
