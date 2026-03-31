"""
并行策略

所有步骤同时执行，子 session 完全独立。
全部完成后把各步骤结果汇总，追加一条消息到主 session，交主 agent 整合。
"""
import logging
from .base import PlanExecutionStrategy
from qrclaw.graph.executor import execute_plan, format_results

logger = logging.getLogger(__name__)


class ParallelStrategy(PlanExecutionStrategy):

    def execute(self, plan, console, run_step_fn, react_loop_fn, session) -> str:
        logger.info(f"并行策略执行计划: {plan.goal}")

        results = execute_plan(plan, console, run_step_fn)

        summary = format_results(plan, results)
        session.add({
            "role": "user",
            "content": (
                f"以上是任务「{plan.goal}」的各步骤执行结果，"
                f"请根据这些结果给出最终的整合回复。\n\n{summary}"
            ),
        })

        logger.info("并行计划执行完毕，进入主 agent 整合")
        return react_loop_fn()
