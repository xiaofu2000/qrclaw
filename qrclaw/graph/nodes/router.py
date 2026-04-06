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
import json
import re
from dataclasses import dataclass, field
from qrclaw.providers import provider
from qrclaw.memory.context.context_manager import get_context_manager
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.router")

# Router 的 system prompt
_ROUTER_PROMPT = """你是一个任务路由器。

根据用户的输入，判断应该使用哪种执行方式：

1. direct（直接执行）：适合简单、明确的任务
   - 单步骤操作（如读文件、写文件、运行命令）
   - 明确的问答
   - 不需要多步骤或并行处理

2. plan（计划执行）：适合复杂、需要多步骤的任务
   - 需要多步骤才能完成
   - 需要探索未知结构（目录、代码库）
   - 需要并行处理多个独立子任务
   - 任务目标不明确，需要拆解

输出格式（必须是有效的 JSON）：
{
    "route": "direct" 或 "plan",
    "goal": "任务目标（plan 模式必填）",
    "project_path": "项目根目录绝对路径（如有）",
    "steps": [
        {"id": "1", "description": "步骤描述", "depends_on": []},
        {"id": "2", "description": "步骤描述", "depends_on": ["1"]}
    ]
}

注意：
- route 为 direct 时，goal 和 steps 可以省略或为空
- depends_on 为空数组表示无依赖，可并行执行
- project_path 填写推测的项目根目录路径
"""


# 路由指令
_ROUTE_INSTRUCTION = """请判断这个任务应该使用哪种执行方式，直接输出 JSON。"""


@dataclass
class PlanStep:
    id: str
    description: str
    depends_on: list = field(default_factory=list)


@dataclass
class Plan:
    goal: str
    steps: list
    project_path: str = ""


@dataclass
class RouteResult:
    route: str  # "direct" | "plan"
    plan: Plan | None = None


def _parse_json(raw: str) -> dict:
    """从 LLM 输出中提取 JSON"""
    # 去掉 <result> 标签（MiniMax 等 thinking 模型会返回）
    raw = re.sub(r'<think>.*?
</think>', '', raw, flags=re.DOTALL).strip()
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
    project_path = data.get("project_path", "")
    steps = [
        PlanStep(
            id=s["id"],
            description=s["description"],
            depends_on=s.get("depends_on", []),
        )
        for s in data.get("steps", [])
    ]
    return Plan(goal=goal, steps=steps, project_path=project_path)


class RouterNode:

    def run(self, user_input: str) -> RouteResult:
        logger.info(f"Router 判断路由: {user_input[:60]}...")
        ctx = get_context_manager()
        messages = ctx.build_messages("router", route_instruction=_ROUTE_INSTRUCTION)

        try:
            # 强制 JSON 输出 + 低温度
            response = provider.chat(
                messages,
                tools=None,
                json_mode=True,
                temperature=0.1,
            )
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
                logger.warning(f"路由结果: plan，目标: {p.goal}，项目路径: {p.project_path}，共 {len(p.steps)} 步")
                for s in p.steps:
                    dep_str = f"依赖 {s.depends_on}" if s.depends_on else "可并行"
                    logger.debug(f"  Step {s.id}: {s.description} [{dep_str}]")
                return RouteResult(route="plan", plan=p)

            logger.info("路由结果: direct")
            return RouteResult(route="direct")

        except Exception as e:
            logger.warning(f"Router 解析失败: {e}，降级为 direct")
            return RouteResult(route="direct")
