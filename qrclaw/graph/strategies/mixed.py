"""
混合策略

既有串行层也有并行层。
- 串行层：子 session 继承主 session 消息，执行完增量追加回主 session
- 并行层：子 session 完全独立，全层完成后汇总结果
最后一层是串行时直接返回结果；最后一层是并行时交主 agent 整合。
"""
import logging
from .base import PlanExecutionStrategy
from qrclaw.graph.executor import execute_plan, format_results, _topological_layers

logger = logging.getLogger(__name__)


class MixedStrategy(PlanExecutionStrategy):

    def execute(self, plan, console, run_step_fn, react_loop_fn, session) -> str:
        logger.info(f"混合策略执行计划: {plan.goal}")

        results = execute_plan(plan, console, run_step_fn)

        layers = _topological_layers(plan.steps)
        last_layer_is_parallel = len(layers[-1]) > 1

        if last_layer_is_parallel:
            summary = format_results(plan, results)
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
            logger.info("混合计划最后一层为串行，最后一步结果即为最终答案")
            return results.get(plan.steps[-1].id, "")
