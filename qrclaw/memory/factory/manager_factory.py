"""
记忆管理器工厂

统一管理 MemoryManager、MemoryIndexer 的创建。
确保所有记忆相关组件使用一致的路径配置。
"""
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qrclaw.memory.core.memory_manager import MemoryManager
    from qrclaw.memory.storage.indexer import MemoryIndexer

# 统一使用 agents 目录结构
AGENTS_ROOT = Path.home() / ".qrclaw" / "agents"


def get_default_memory_dir(agent_id: str = None) -> Path:
    """
    获取默认记忆目录
    
    统一路径格式：~/.qrclaw/agents/{agent_id}/memory/
    
    Args:
        agent_id: agent 标识符，默认为 "default"
        
    Returns:
        Path: 记忆目录路径
    """
    if agent_id:
        return AGENTS_ROOT / agent_id / "memory"
    return AGENTS_ROOT / "default" / "memory"


def create_memory_manager(
    agent_id: str = None,
    memory_dir: Path = None,
) -> "MemoryManager":
    """
    创建 MemoryManager 实例
    
    优先级：memory_dir > agent_id > default
    
    Args:
        agent_id: agent 标识符
        memory_dir: 直接指定记忆目录路径
        
    Returns:
        MemoryManager: 配置好的记忆管理器
    """
    from qrclaw.memory.core.memory_manager import MemoryManager
    
    base_dir = memory_dir or get_default_memory_dir(agent_id)
    return MemoryManager(memory_dir=base_dir)


def create_memory_indexer(
    agent_id: str = None,
    memory_dir: Path = None,
) -> "MemoryIndexer":
    """
    创建 MemoryIndexer 实例
    
    Args:
        agent_id: agent 标识符
        memory_dir: 直接指定记忆目录路径
        
    Returns:
        MemoryIndexer: 配置好的记忆索引器
    """
    from qrclaw.memory.storage.indexer import MemoryIndexer
    
    base_dir = memory_dir or get_default_memory_dir(agent_id)
    return MemoryIndexer(memory_dir=base_dir)


def create_long_term_memory(
    agent_id: str = None,
    memory_dir: Path = None,
):
    """
    创建 LongTermMemory 实例
    
    整合 MemoryManager 和 MemoryIndexer 的便捷方法。
    
    Args:
        agent_id: agent 标识符
        memory_dir: 直接指定记忆目录路径
        
    Returns:
        LongTermMemory: 完整的记忆管理系统
    """
    from qrclaw.memory import LongTermMemory
    
    base_dir = memory_dir or get_default_memory_dir(agent_id)
    return LongTermMemory(memory_dir=base_dir)
