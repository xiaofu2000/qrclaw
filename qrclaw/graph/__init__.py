"""
Graph 模块

为 QRClaw 提供图结构执行能力：
- RouterPlanner：一次 LLM 调用完成路由判断 + 计划生成
- Executor：拓扑排序，自动串行/并行执行
"""
from .router_planner import route_and_plan, RouteResult, Plan, PlanStep
from .executor import execute_plan, format_results

__all__ = [
    "route_and_plan", "RouteResult", "Plan", "PlanStep",
    "execute_plan", "format_results",
]
