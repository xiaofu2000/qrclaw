"""
Router 节点

一次 LLM 调用完成路由判断 + 计划生成：
- 简单任务：返回 RouteResult(route="direct")
- 复杂任务：返回 RouteResult(route="plan", plan=Plan(...))

消息结构：
  [system] 主 agent 完整 system prompt（工作目录、工具、行为准则全有）
  [history] 主 session 的历史对话
  [user]   路由指令 + 判断规则 + JSON 格式要求

JSON 可靠性保障（双重防御）：
  1. json_mode=True：OpenAI 协议层强制输出合法 JSON
  2. 正则提取兜底：Vertex AI 等不支持 json_mode 的 provider 使用
"""
import json
import re
from dataclasses import dataclass, field
from qrclaw.providers import provider
from qrclaw.memory.context_manager import get_context_manager
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.router")

_ROUTE_INSTRUCTION = """【系统指令】根据以上对话，判断最新一条用户消息是否需要制定执行计划，只返回 JSON，不要其他内容。

【判断规则】
需要计划（route="plan"）的情况：
1. 任务含 3 个及以上明确步骤
2. 任务涉及多个文件、模块或方向，可以并行处理
3. 任务需要先探索环境再决定后续步骤
4. 任务明确要求分阶段完成

不需要计划（route="direct"）的情况：
1. 简单问答、解释、翻译
2. 单个文件操作
3. 单条命令执行
4. 闲聊

【输出格式】
必须先输出 "thought" 字段进行逻辑推理，再输出 route 结论。

简单任务只返回：
{
  "thought": "简要分析用户的意图，说明为什么这是一个简单任务。",
  "route": "direct"
}

复杂任务返回（同时生成执行计划）：
{
  "thought": "分析任务的复杂度和包含的物理步骤，梳理出需要并行的模块和依赖关系。",
  "route": "plan",
  "goal": "任务目标的简短描述",
  "steps": [
    {"id": 1, "description": "步骤描述", "depends_on": []},
    {"id": 2, "description": "步骤描述", "depends_on": [1]},
    {"id": 3, "description": "步骤描述", "depends_on": []},
    {"id": 4, "description": "步骤描述", "depends_on": [2, 3]}
  ]
}

【规划规则】
- 每个步骤具体、可执行，一步只做一件事
- depends_on 填前置步骤 id，没有依赖填空数组
- 无依赖的步骤会被并行执行，有依赖的步骤串行等待
- 【动态规划视野（战争迷雾）】：你不必强制生成全部步骤。如果缺乏上下文（如未知目录），只需生成 1~2 个探测步骤（如 ls, find），绝不要凭空猜测后续！如果情报充足，请尽可能多地规划可并行的独立步骤。
- 需要汇总或综合分析前置步骤结果的步骤，必须在 depends_on 中列出所有它依赖的步骤 id
- 【上下文隔离与防幻觉原则】：执行步骤的子 agent 看不到对话历史，因此对于用户明确提供的已知信息（目录、参数等），必须直接写入描述中实现自包含。
- 【严禁瞎编具体细节】：对于需要前置步骤（depends_on）动态搜索才能得知的未知信息（如具体文件名），绝对禁止在描述中盲目猜测或举例（例如禁止写“如 session.py”）。必须指示子 agent：“使用前置步骤 [id] 传递过来的结果进行处理”。
- 步骤描述要足够详细，相当于给一个全新的 agent 下达完整任务指令：包括做什么、怎么做、目标是什么、输出什么"""


@dataclass
class PlanStep:
    id: int
    description: str
    depends_on: list[int] = field(default_factory=list)
    done: bool = False


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep]

    def get_step(self, step_id: int) -> PlanStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def mark_done(self, step_id: int):
        step = self.get_step(step_id)
        if step:
            step.done = True

    def all_done(self) -> bool:
        return all(s.done for s in self.steps)


@dataclass
class RouteResult:
    route: str               # "direct" | "plan"
    plan: Plan | None = None  # route=plan 时携带完整 Plan


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"无法从输出中提取 JSON: {raw[:100]}")


def _parse_plan(data: dict, fallback_input: str) -> Plan:
    goal = data.get("goal", fallback_input[:50])
    steps = [
        PlanStep(
            id=s["id"],
            description=s["description"],
            depends_on=s.get("depends_on", []),
        )
        for s in data.get("steps", [])
    ]
    return Plan(goal=goal, steps=steps)


class RouterNode:

    def run(self, user_input: str) -> RouteResult:
        logger.info(f"Router 判断路由: {user_input[:60]}...")
        ctx = get_context_manager()
        messages = ctx.build_messages("router", route_instruction=_ROUTE_INSTRUCTION)

        try:
            response = provider.chat(messages, tools=None, json_mode=True)
            data = _parse_json(response.content)

            route_val = data.get("route", "direct")
            if route_val not in ("direct", "plan"):
                logger.warning(f"Router 返回未知 route: {route_val}，降级为 direct")
                route_val = "direct"

            if route_val == "plan":
                if not data.get("steps"):
                    logger.warning("Router 返回 plan 但 steps 为空，降级为 direct")
                    return RouteResult(route="direct")
                p = _parse_plan(data, user_input)
                logger.info(f"路由结果: plan，目标: {p.goal}，共 {len(p.steps)} 步")
                for s in p.steps:
                    dep_str = f"依赖 {s.depends_on}" if s.depends_on else "可并行"
                    logger.debug(f"  Step {s.id}: {s.description} [{dep_str}]")
                return RouteResult(route="plan", plan=p)

            logger.info("路由结果: direct")
            return RouteResult(route="direct")

        except Exception as e:
            logger.warning(f"Router 解析失败: {e}，降级为 direct")
            return RouteResult(route="direct")
