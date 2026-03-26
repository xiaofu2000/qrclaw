from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.planning")

class PlanStep(BaseModel):
    id: int = Field(description="步骤编号，从 1 开始")
    description: str = Field(description="这一步要做什么")
    depends_on: list[int] = Field(default=[], description="依赖的前置步骤编号列表，没有依赖则为空")

class CreatePlanArgs(BaseModel):
    goal: str = Field(description="任务目标，简要描述要完成什么")
    steps: list[PlanStep] = Field(description="执行步骤列表，按顺序排列")

class CompleteStepArgs(BaseModel):
    step_id: int = Field(description="已完成的步骤编号")

@register(description="为复杂任务创建执行计划，拆解成有序步骤后逐步执行", args_model=CreatePlanArgs)
def create_plan(goal: str, steps: list[dict]) -> str:
    from qrclaw.agent import get_session
    logger.info(f"创建执行计划: {goal}, 共 {len(steps)} 步")
    session = get_session()
    if session:
        session.set_plan(goal, steps)
    return f"计划已创建，目标：{goal}，共 {len(steps)} 步。计划已注入上下文，请从 Step 1 开始执行，每完成一步调用 complete_step 标记完成后再继续下一步。"

@register(description="标记某个计划步骤为已完成，完成后继续执行下一步", args_model=CompleteStepArgs)
def complete_step(step_id: int) -> str:
    from qrclaw.agent import get_session
    session = get_session()
    if not session or not session.active_plan:
        return "当前没有活跃的执行计划"
    all_done = session.complete_step(step_id)
    if all_done:
        logger.info(f"步骤 {step_id} 完成，所有步骤已全部完成")
        return f"Step {step_id} 已完成。所有步骤全部完成，计划结束。"
    # 找下一个未完成的步骤
    remaining = [s for s in session.active_plan["steps"] if not s["done"]]
    next_step = remaining[0]
    logger.info(f"步骤 {step_id} 完成，下一步: Step {next_step['id']}")
    return f"Step {step_id} 已完成。请继续执行 Step {next_step['id']}: {next_step['description']}"
