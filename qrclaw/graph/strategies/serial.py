"""
纯串行策略

所有层都是单步，步骤逐个执行，每步结果通过 context 传给下一步。
最后一步执行完直接就是最终答案，不需要主 agent 整合。
"""
import logging
from .base import PlanExecutionStrategy
from qrclaw.graph.executor import execute_plan

logger = logging.getLogger(__name__)


class SerialStrategy(PlanExecutionStrategy):

    def execute(self, plan, session, console, workspace, auto_confirm, run_step_fn, react_loop_fn) -> str:
        logger.info(f"串行策略执行计划: {plan.goal}")

        results = execute_plan(plan, console, run_step_fn)

        # 串行步骤通过 context 逐步传递，最后一步执行完直接是答案，无需主 agent 整合
        logger.info("串行计划执行完毕，最后一步结果即为最终答案")
        return results.get(plan.steps[-1].id, "")
