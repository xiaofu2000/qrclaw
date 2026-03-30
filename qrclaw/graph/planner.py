"""
Planner 节点

进入规划节点后，用一次 LLM 调用生成带依赖关系的完整 Plan。
输出结构化的步骤列表，每个步骤标注 depends_on，
由执行引擎（executor.py）负责解析依赖、决定串行还是并行。
"""
import json
from dataclasses import dataclass, field
from qrclaw.providers import provider
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.planner")

_PLANNER_SYSTEM = """你是一个任务规划师，把用户的复杂任务拆解成可执行的步骤列表。

规则：
1. 每个步骤要具体、可执行，一步只做一件事
2. 用 depends_on 标注依赖关系（填前置步骤的 id），没有依赖填空数组
3. 没有依赖关系的步骤会被并行执行，有依赖的步骤会等前置步骤完成后串行执行
4. 步骤数量控制在 2~10 个

只返回 JSON，格式：
{
  "goal": "任务目标的简短描述",
  "steps": [
    {"id": 1, "description": "步骤描述", "depends_on": []},
    {"id": 2, "description": "步骤描述", "depends_on": [1]},
    {"id": 3, "description": "步骤描述", "depends_on": []},
    {"id": 4, "description": "步骤描述", "depends_on": [2, 3]}
  ]
}

不要返回任何其他内容。"""


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


def plan(user_input: str) -> Plan:
    """
    为复杂任务生成带依赖关系的执行计划。

    Args:
        user_input: 用户原始输入
    Returns:
        Plan 对象
    """
    logger.info(f"Planner 生成计划: {user_input[:80]}...")

    messages = [
        {"role": "system", "content": _PLANNER_SYSTEM},
        {"role": "user", "content": user_input},
    ]

    try:
        response = provider.chat(messages, tools=None)
        raw = response.content.strip()

        # 从 markdown 代码块提取 JSON
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data = json.loads(raw)
        goal = data.get("goal", user_input[:50])
        steps = [
            PlanStep(
                id=s["id"],
                description=s["description"],
                depends_on=s.get("depends_on", []),
            )
            for s in data.get("steps", [])
        ]

        logger.info(f"计划生成成功: {goal}，共 {len(steps)} 步")
        for s in steps:
            dep_str = f"依赖 {s.depends_on}" if s.depends_on else "无依赖（可并行）"
            logger.debug(f"  Step {s.id}: {s.description} [{dep_str}]")

        return Plan(goal=goal, steps=steps)

    except Exception as e:
        # 解析失败时生成一个单步兜底 Plan，保证流程不中断
        logger.error(f"Planner 解析失败: {e}，生成兜底计划")
        return Plan(
            goal=user_input[:50],
            steps=[PlanStep(id=1, description=user_input, depends_on=[])],
        )
