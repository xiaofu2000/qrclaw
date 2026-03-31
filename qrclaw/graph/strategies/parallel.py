"""
纯并行策略

只有一层且多步，所有步骤同时执行。
全部跑完后结果是散的，需要主 agent 做最终整合。
"""
import logging
from .base import PlanExecutionStrategy
from qrclaw.graph.executor import execute_plan, format_results

logger = logging.getLogger(__name__)


class ParallelStrategy(PlanExecutionStrategy):

    def execute(self, plan, session, console, workspace, auto_confirm, run_step_fn, react_loop_fn) -> str:
        logger.info(f"并行策略执行计划: {plan.goal}")

        results = execute_plan(plan, console, run_step_fn)

        summary = format_results(plan, results)
        # 并行结果散落，主 agent 没参与过程，需要汇总后整合
        session.add({
            "role": "user",
            "content": (
                f"以上是任务「{plan.goal}」的各步骤执行结果，"
                f"请根据这些结果给出最终的整合回复。\n\n{summary}"
            ),
        })

        logger.info("并行计划执行完毕，进入主 agent 整合")
        return react_loop_fn()
