"""
串行策略

所有层都是单步，步骤逐个执行。
每步子 session 继承主 session 的完整消息历史，执行完增量追加回主 session。
最后一步结果直接作为最终答案。
"""
import logging
from .base import PlanExecutionStrategy
from qrclaw.graph.executor import execute_plan

logger = logging.getLogger(__name__)


class SerialStrategy(PlanExecutionStrategy):

    def execute(self, plan, console, run_step_fn, react_loop_fn, session) -> str:
        logger.info(f"串行策略执行计划: {plan.goal}")

        results = execute_plan(plan, console, run_step_fn)

        logger.info("串行计划执行完毕，最后一步结果即为最终答案")
        return results.get(plan.steps[-1].id, "")
