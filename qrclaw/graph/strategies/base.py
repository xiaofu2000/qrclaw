"""
计划执行策略基类

三种策略：
- SerialStrategy：所有层都是单步，纯串行
- ParallelStrategy：只有一层且多步，纯并行
- MixedStrategy：混合，有串行也有并行
"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console
    from qrclaw.graph.nodes.router import Plan
    from qrclaw.memory.session import Session


class PlanExecutionStrategy(ABC):

    @abstractmethod
    def execute(
        self,
        plan: "Plan",
        console: "Console",
        run_step_fn,
        react_loop_fn,
        session: "Session",
    ) -> str:
        """执行计划，返回最终回复字符串"""
        ...
