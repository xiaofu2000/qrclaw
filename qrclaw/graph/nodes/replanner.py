"""
Replanner 节点

每层步骤执行完后，基于真实执行结果重新评估剩余计划：
- 如果目标已达成，返回 None（DONE）
- 如果需要调整方向，返回新的步骤列表
- 如果原计划仍合理，返回原剩余步骤不变
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field
from qrclaw.prompts import REPLANNER_INSTRUCTION
from qrclaw.providers.litellm_provider import LiteLLMProvider
from qrclaw.llm_service import get_llm_service
from qrclaw.graph.nodes.router import PlanStep
from qrclaw.memory.context.context_manager import get_context_manager
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.replanner")


# ── Pydantic Schema ────────────────────────────────────────────────────────────

class ReplanStepSchema(BaseModel):
    id: int
    description: str
    depends_on: list[int] = Field(default_factory=list)


class ReplanSchema(BaseModel):
    thought: str = Field(description="逻辑推演过程，包含对战报的分析")
    status: Literal["done", "continue"]
    project_path: str = Field(default="", description="项目根目录绝对路径，status=continue 时填写")
    steps: list[ReplanStepSchema] = Field(default_factory=list, description="新的剩余步骤，status=continue 时填写")


# ── ReplannerNode ──────────────────────────────────────────────────────────────

class ReplannerNode:

    def run(self) -> list[PlanStep] | None:
        """
        评估并重规划。状态全部从 get_context_manager().plan_state 读取。

        Returns:
            None: 目标已达成（DONE）
            list[PlanStep]: 新的剩余步骤
        """
        ctx = get_context_manager()
        ps = ctx.plan_state
        logger.info(f"Replanner 评估，已完成 {len(ps.past_steps)} 步，剩余 {len(ps.remaining)} 步")

        messages = ctx.build_messages("replanner", replanner_instruction=REPLANNER_INSTRUCTION)

        try:
            llm = get_llm_service()
            if not llm.is_provider_type(LiteLLMProvider):
                raise RuntimeError("Replanner 目前仅支持 LiteLLMProvider")

            import instructor
            from litellm import completion
            # 使用 MD_JSON 模式，避免依赖 response_format=json_object（部分模型不支持）
            client = instructor.from_litellm(completion, mode=instructor.Mode.MD_JSON)

            kwargs = llm.make_instructor_kwargs(messages, temperature=0.1)
            kwargs["response_model"] = ReplanSchema
            kwargs["max_retries"] = 3

            result: ReplanSchema = client.chat.completions.create(**kwargs)

        except Exception as e:
            logger.warning(f"Replanner 调用失败: {e}，保持原计划继续")
            return ctx.plan_state.remaining

        logger.debug(f"Replanner thought: {result.thought}")

        if result.status == "done":
            logger.warning("Replanner 判断目标已达成，DONE")
            return None

        # 如果 LLM 重新确认了项目路径，更新 PlanState
        if result.project_path and result.project_path != ps.project_path:
            logger.info(f"Replanner 更新项目路径: {result.project_path}")
            ps.project_path = result.project_path

        if not result.steps:
            logger.info("Replanner 返回空步骤，视为 DONE")
            return None

        new_steps = [
            PlanStep(
                id=s.id,
                description=s.description,
                depends_on=s.depends_on,
            )
            for s in result.steps
        ]

        if len(new_steps) != len(ps.remaining) or any(
            n.description != o.description for n, o in zip(new_steps, ps.remaining)
        ):
            logger.info(f"Replanner 调整了计划，新剩余步骤数: {len(new_steps)}")
        else:
            logger.info("Replanner 确认原计划不变，继续执行")

        return new_steps
