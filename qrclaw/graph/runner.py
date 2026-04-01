"""
GraphRunner —图的入口

负责节点编排和条件边路由：

    Router ──→ ReactLoop       （route=direct，简单任务）
           └─→ PlanExecutor    （route=plan，复杂任务）
                   └─→ ReactLoop （并行计划完成后整合，由策略决定）
"""
from rich.console import Console
from qrclaw.memory.session import Session
from qrclaw.workspace import Workspace
from qrclaw.graph.nodes.router import RouterNode
from qrclaw.graph.nodes.react_loop import ReactLoopNode
from qrclaw.graph.nodes.plan_executor import PlanExecutorNode
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.runner")


class GraphRunner:

    def __init__(self):
        self.router = RouterNode()
        self.react_loop = ReactLoopNode()
        self.plan_executor = PlanExecutorNode()

    def run(
        self,
        user_input: str,
        session: Session,
        console: Console,
        workspace: Workspace,
        auto_confirm: bool = False,
        is_sub_agent: bool = False,
        run_sub_agent_fn=None,
    ) -> str:
        # 子 agent 跳过路由，直接走 ReactLoop
        if is_sub_agent:
            logger.info("子 agent 跳过路由，直接走 ReactLoop")
            return self.react_loop.run(session, console, workspace, auto_confirm, is_sub_agent=True)

        # 条件边：Router 判断路由
        route_result = self.router.run(user_input)

        if route_result.route == "plan" and route_result.plan:
            logger.info(f"路由 → PlanExecutor: {route_result.plan.goal}")

            # Router 生成 plan 后直接写入 ctx，下游不需再传递 plan 对象
            from qrclaw.memory.context_manager import get_context_manager
            get_context_manager().set_plan(route_result.plan.goal, route_result.plan.steps)

            def react_loop_fn():
                return self.react_loop.run(session, console, workspace, auto_confirm, is_sub_agent=False)

            return self.plan_executor.run(
                session=session,
                console=console,
                workspace=workspace,
                auto_confirm=auto_confirm,
                run_sub_agent_fn=run_sub_agent_fn,
                react_loop_fn=react_loop_fn,
            )

        # 条件边：route=direct
        logger.info("路由 → ReactLoop（直接执行）")
        return self.react_loop.run(session, console, workspace, auto_confirm, is_sub_agent=False)
