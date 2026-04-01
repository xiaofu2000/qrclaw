"""
上下文管理模块

包含：
- Session: 会话级记忆
- ContextManager: 上下文管理器
- StepResult: 步骤结果数据结构
"""

# 基础组件（不依赖 qrclaw.prompt）
from qrclaw.memory.context.session import Session
from qrclaw.memory.context.step_result import StepResult

# ContextManager 需要延迟导入以避免循环依赖
def _get_context_manager():
    from qrclaw.memory.context.context_manager import ContextManager
    return ContextManager

ContextManager = None  # 延迟初始化

__all__ = [
    "Session",
    "ContextManager",
    "StepResult",
]
