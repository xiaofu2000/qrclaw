"""
Router 节点

一次 LLM 调用完成路由判断 + 计划生成：
- 简单任务：返回 RouteResult(route="direct")
- 复杂任务：返回 RouteResult(route="plan", plan=Plan(...))

JSON 可靠性保障（双重防御）：
  1. json_mode=True：OpenAI 协议层强制输出合法 JSON
  2. 正则提取兜底：Vertex AI 等不支持 json_mode 的 provider 使用
"""
import json
import re
from dataclasses import dataclass, field
from qrclaw.providers import provider
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.router")

_SYSTEM_TEMPLATE = """你是一个任务路由和规划器，判断用户最新任务是否需要制定执行计划。
如果有历史对话，结合上下文理解用户意图再判断。

【当前工作环境】
工作目录：{workspace_dir}
代码/文件已在本地，无需克隆或下载。

【判断规则】
需要计划（route=plan）的情况：
1. 任务含 3 个及以上明确步骤
2. 任务涉及多个文件、模块或方向，可以并行处理
3. 任务需要先探索环境再决定后续步骤
4. 任务明确要求分阶段完成

不需要计划（route=direct）的情况：
1. 简单问答、解释、翻译
2. 单个文件操作
3. 单条命令执行
4. 闲聊

【输出格式】
简单任务只返回：
{"route": "direct"}

复杂任务返回（同时生成执行计划）：
{
  "route": "plan",
  "goal": "任务目标的简短描述",
  "steps": [
    {"id": 1, "description": "步骤描述", "depends_on": []},
    {"id": 2, "description": "步骤描述", "depends_on": [1]},
    {"id": 3, "description": "步骤描述", "depends_on": []},
    {"id": 4, "description": "步骤描述", "depends_on": [2, 3]}
  ]
}

规划规则：
- 每个步骤具体、可执行，一步只做一件事
- depends_on 填前置步骤 id，没有依赖填空数组
- 无依赖的步骤会被并行执行，有依赖的步骤串行等待
- 步骤数量控制在 2~10 个
- 需要汇总或综合分析前置步骤结果的步骤，必须在 depends_on 中列出所有它依赖的步骤 id，否则它执行时拿不到前置结果
- 步骤描述必须自包含：将执行所需的关键信息（文件路径、目录、参数、约束条件等）直接写入描述中，因为执行该步骤的子 agent 看不到对话历史
- 步骤描述要足够详细，相当于给一个全新的 agent 下达完整任务指令：包括做什么、怎么做、目标是什么、输出什么结果，不能只写一句话标题

只返回 JSON，不要其他内容。"""


def _build_system(workspace_dir: str) -> str:
    return _SYSTEM_TEMPLATE.replace("{workspace_dir}", workspace_dir)


_ROUTE_INSTRUCTION = (
    "【系统指令】根据以上对话，判断最新一条用户消息是否需要制定执行计划，"
    "按格式返回 JSON。"
)


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

    def run(self, user_input: str, history: list, workspace_dir: str = "") -> RouteResult:
        logger.info(f"Router 判断路由: {user_input[:60]}...")

        messages: list[dict] = [{"role": "system", "content": _build_system(workspace_dir)}]
        if history:
            messages.extend(history)
        else:
            messages.append({"role": "user", "content": user_input})
        messages.append({"role": "user", "content": _ROUTE_INSTRUCTION})

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
