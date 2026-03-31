"""
混合策略

既有并行层也有串行层。
最后一层是串行时：最后一步执行完直接是最终答案。
最后一层是并行时：需要主 agent 整合。
"""
import logging
from .base import PlanExecutionStrategy
from qrclaw.graph.executor import execute_plan, format_results, _topological_layers

logger = logging.getLogger(__name__)


class MixedStrategy(PlanExecutionStrategy):

    def execute(self, plan, session, console, workspace, auto_confirm, run_step_fn, react_loop_fn) -> str:
        logger.info(f"混合策略执行计划: {plan.goal}")

        results = execute_plan(plan, console, run_step_fn)

        summary = format_results(plan, results)
        layers = _topological_layers(plan.steps)
        last_layer_is_parallel = len(layers[-1]) > 1

        if last_layer_is_parallel:
            # 最后一层是并行，结果散落，需要主 agent 整合
            session.add({
                "role": "user",
                "content": (
                    f"以上是任务「{plan.goal}」的各步骤执行结果，"
                    f"请根据这些结果给出最终的整合回复。\n\n{summary}"
                ),
            })
            logger.info("混合计划最后一层为并行，进入主 agent 整合")
            return react_loop_fn()
        else:
            # 最后一层是串行，最后一步执行完直接是答案
            logger.info("混合计划最后一层为串行，最后一步结果即为最终答案")
            return results.get(plan.steps[-1].id, "")
