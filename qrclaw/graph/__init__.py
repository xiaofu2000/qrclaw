"""
Graph 模块

图结构执行能力：
- nodes/router.py：路由判断 + 计划生成
- nodes/react_loop.py：主 agent ReAct 循环
- nodes/plan_executor.py：计划执行（串行/并行/混合策略）
- runner.py：图入口，节点编排 + 条件边路由
- executor.py：拓扑排序执行引擎
- strategies/：串行/并行/混合执行策略
"""
from .nodes.router import RouterNode, RouteResult, Plan, PlanStep
from .nodes.react_loop import ReactLoopNode
from .nodes.plan_executor import PlanExecutorNode
from .executor import execute_plan, format_results
from .runner import GraphRunner

__all__ = [
    "RouterNode", "RouteResult", "Plan", "PlanStep",
    "ReactLoopNode", "PlanExecutorNode",
    "execute_plan", "format_results",
    "GraphRunner",
]
