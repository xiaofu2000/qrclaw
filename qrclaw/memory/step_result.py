"""
步骤结果兼容层

将 step_result 保留在根目录以保持向后兼容。
实际的实现现在位于 context/step_result.py
"""

from qrclaw.memory.context.step_result import StepResult

__all__ = ["StepResult"]
