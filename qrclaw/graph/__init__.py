"""
Graph 模块

为 QRClaw 提供图结构执行能力：
- Router：判断任务是否需要规划
- Planner：生成带依赖关系的执行计划
- Executor：拓扑排序，自动串行/并行执行
"""
from .router import route, RouteResult
from .planner import plan, Plan, PlanStep
from .executor import execute_plan, format_results

__all__ = [
    "route", "RouteResult",
    "plan", "Plan", "PlanStep",
    "execute_plan", "format_results",
]
