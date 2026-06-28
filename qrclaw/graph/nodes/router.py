"""
Router 节点 —— 判断用户意图路由

职责：
- 分析用户输入，判断是简单任务还是复杂任务
- 简单任务 → 直接执行（ReactLoop）
- 复杂任务 → 计划执行（PlanExecutor + ReactLoop）

路由判断基于：
- 用户输入的复杂度
- 是否需要多步骤
- 是否需要探索/并行
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

from qrclaw.providers.litellm_provider import LiteLLMProvider
from qrclaw.llm_service import get_llm_service
from qrclaw.memory.context.context_manager import get_context_manager
from qrclaw.memory.wiki.wiki_memory import WikiMemory
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.router")

# 用户指令
_ROUTE_INSTRUCTION = """请根据以上对话判断并输出 JSON。"""


def _get_instructor_client():
    """懒加载 instructor client，避免顶层 import 影响未使用 litellm 的环境。"""
    import instructor
    from litellm import completion
    # 使用 MD_JSON 模式：让模型在 markdown 代码块中返回 JSON
    # 避免使用 JSON_MODE（需要 response_format=json_object），部分模型不支持该参数
    return instructor.from_litellm(completion, mode=instructor.Mode.MD_JSON)


# ── Pydantic Schema ────────────────────────────────────────────────────────────

class PlanStepSchema(BaseModel):
    id: int
    description: str
    depends_on: list[int] = Field(default_factory=list)


class RouteSchema(BaseModel):
    thought: str = Field(description="简要分析任务复杂度的推理过程")
    route: Literal["direct", "plan"]
    project_path: str = Field(default="", description="项目根目录绝对路径，route=plan 时填写")
    goal: str = Field(default="", description="任务目标简短描述，route=plan 时填写")
    steps: list[PlanStepSchema] = Field(default_factory=list, description="执行步骤，route=plan 时填写")


# ── 对外数据类（保持兼容） ──────────────────────────────────────────────────────

class PlanStep:
    def __init__(self, id: str, description: str, depends_on: list | None = None):
        self.id = id
        self.description = description
        self.depends_on = depends_on or []


class Plan:
    def __init__(self, goal: str, steps: list, project_path: str = ""):
        self.goal = goal
        self.steps = steps
        self.project_path = project_path


class RouteResult:
    def __init__(self, route: str, plan: Plan | None = None):
        self.route = route
        self.plan = plan


# ── RouterNode ────────────────────────────────────────────────────────────────

class RouterNode:

    def run(self, user_input: str) -> RouteResult:
        logger.info(f"Router 判断路由: {user_input[:60]}...")
        ctx = get_context_manager()
        messages = ctx.build_messages("router", route_instruction=_ROUTE_INSTRUCTION)

        try:
            llm = get_llm_service()
            if not llm.is_provider_type(LiteLLMProvider):
                raise RuntimeError("Router 目前仅支持 LiteLLMProvider")

            client = _get_instructor_client()
            kwargs = llm.make_instructor_kwargs(messages, temperature=0.1)
            kwargs["response_model"] = RouteSchema
            kwargs["max_retries"] = 3

            result: RouteSchema = client.chat.completions.create(**kwargs)

            logger.debug(f"Router thought: {result.thought}")

            if result.route == "plan":
                if not result.steps:
                    logger.warning("Router 返回 plan 但 steps 为空，降级为 direct")
                    return RouteResult(route="direct")

                steps = [
                    PlanStep(id=str(s.id), description=s.description, depends_on=[str(d) for d in s.depends_on])
                    for s in result.steps
                ]
                plan = Plan(goal=result.goal, steps=steps, project_path=result.project_path)
                logger.info(f"路由结果: plan，目标: {plan.goal}，项目路径: {plan.project_path}，共 {len(steps)} 步")
                for s in steps:
                    dep_str = f"依赖 {s.depends_on}" if s.depends_on else "可并行"
                    logger.debug(f"  Step {s.id}: {s.description} [{dep_str}]")

                # Plan 模式：查询 Wiki 并注入后续 Agent 的 System Prompt
                wiki_context = _query_wiki_context(result.goal, messages)
                ctx = get_context_manager()
                ctx.set_plan(plan.goal, plan.steps, plan.project_path)
                ctx.set_wiki_context(wiki_context)  # 标记 System Prompt 待重建

                return RouteResult(route="plan", plan=plan)

            # Direct 模式：不查 Wiki，快速执行
            logger.info("路由结果: direct")
            return RouteResult(route="direct")

        except Exception as e:
            logger.warning(f"Router 解析失败: {e}，降级为 direct")
            return RouteResult(route="direct")


def _query_wiki_context(goal: str, messages: list[dict]) -> str:
    """
    查询 Wiki 记忆，通过 LLM 精排获取与当前目标相关的页面正文。

    Args:
        goal: 任务目标描述（用于 Wiki 查询）
        messages: 当前 session 的 messages（用于 LLM 上下文理解）

    Returns:
        格式化后的 Wiki 正文内容，无相关内容时返回空字符串
    """
    # 延迟导入避免循环依赖
    from qrclaw.agent import get_workspace

    try:
        workspace = get_workspace()
        if not workspace or not workspace.memory_dir:
            logger.debug("无 workspace 或 memory_dir，跳过 Wiki 查询")
            return ""

        wiki = WikiMemory.for_workspace(workspace.memory_dir)
        pages = wiki.select_relevant_pages(goal, messages, top_k=3)

        if not pages:
            logger.debug(f"Wiki 查询无结果: {goal}")
            return ""

        # 将页面正文拼接为上下文字符串
        sections = []
        for page in pages:
            sections.append(f"### {page.name}\n{page.content}")

        context = "\n\n---\n\n".join(sections)
        logger.info(f"Wiki 查询命中 {len(pages)} 个页面: {[p.name for p in pages]}")
        return context

    except Exception as e:
        logger.warning(f"Wiki 查询失败: {e}")
        return ""
