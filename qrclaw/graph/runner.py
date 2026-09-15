"""
GraphRunner —图的入口

负责节点编排和条件边路由：

    Router ──→ ReactLoop       （route=direct，简单任务）
           └─→ PlanExecutor    （route=plan，复杂任务）
                   └─→ ReactLoop （并行计划完成后整合，由策略决定）
"""
from rich.console import Console
from qrclaw.memory.context.session import Session
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
        execution_context=None,
    ) -> str:
        # 子 agent 跳过路由，直接走 ReactLoop
        if is_sub_agent:
            logger.info("子 agent 跳过路由，直接走 ReactLoop")
            return self.react_loop.run(
                session,
                console,
                workspace,
                auto_confirm,
                is_sub_agent=True,
                execution_context=execution_context,
            )

        if execution_context is not None:
            execution_context.check_cancelled()
            execution_context.publish("run.status_changed", {"status": "routing"})
            execution_context.publish(
                "agent.progress",
                {"current_action": "正在判断任务执行方式"},
            )

        # 条件边：Router 判断路由
        route_result = self.router.run(user_input)

        if execution_context is not None:
            execution_context.publish("route.decided", {"route": route_result.route})
            execution_context.publish("run.status_changed", {"status": "running"})

        if route_result.route == "plan" and route_result.plan:
            logger.info(f"路由 → PlanExecutor: {route_result.plan.goal}")

            if execution_context is not None:
                execution_context.plan_id = execution_context.new_id("plan")
                execution_context.publish(
                    "plan.created",
                    {
                        "plan_id": execution_context.plan_id,
                        "goal": route_result.plan.goal,
                        "project_path": route_result.plan.project_path,
                        "steps": [
                            {
                                "step_id": str(step.id),
                                "description": step.description,
                                "depends_on": [str(item) for item in step.depends_on],
                                "status": "pending",
                            }
                            for step in route_result.plan.steps
                        ],
                        "revision": 1,
                    },
                )

            def react_loop_fn():
                return self.react_loop.run(
                    session,
                    console,
                    workspace,
                    auto_confirm,
                    is_sub_agent=False,
                    execution_context=execution_context,
                )

            return self.plan_executor.run(
                session=session,
                console=console,
                workspace=workspace,
                auto_confirm=auto_confirm,
                run_sub_agent_fn=run_sub_agent_fn,
                react_loop_fn=react_loop_fn,
                execution_context=execution_context,
            )

        # 条件边：route=direct
        logger.info("路由 → ReactLoop（直接执行）")
        return self.react_loop.run(
            session,
            console,
            workspace,
            auto_confirm,
            is_sub_agent=False,
            execution_context=execution_context,
        )
