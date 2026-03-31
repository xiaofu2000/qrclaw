"""
Replanner 节点

每层步骤执行完后，基于真实执行结果重新评估剩余计划：
- 如果目标已达成，返回 None（DONE）
- 如果需要调整方向，返回新的步骤列表
- 如果原计划仍合理，返回原剩余步骤不变
"""
import json
import re
from qrclaw.providers import provider
from qrclaw.graph.nodes.router import PlanStep
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.replanner")

_REPLANNER_PROMPT = """你是一个任务重规划器。根据已完成步骤的真实结果，评估剩余计划是否需要调整。

【终极目标】
{goal}

【已完成步骤及真实结果】
{past_steps}

【原定剩余步骤】
{remaining_steps}

【你的任务】
1. 分析已完成步骤的真实结果
2. 判断终极目标是否已经达成
3. 判断剩余步骤是否仍然合理，还是需要根据实际情况调整

【输出格式】
如果目标已达成，只返回：
{"status": "done"}

如果需要继续执行（可以保留原计划或调整），返回：
{
  "status": "continue",
  "steps": [
    {"id": 1, "description": "步骤描述", "depends_on": []},
    {"id": 2, "description": "步骤描述", "depends_on": [1]}
  ]
}

注意：
- steps 里的 id 从 1 重新编号
- 只返回 JSON，不要其他内容
- 步骤描述要自包含，包含执行所需的关键信息"""


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"无法解析 Replanner 输出: {raw[:100]}")


class ReplannerNode:

    def run(
        self,
        goal: str,
        past_steps: list[tuple[str, str]],
        remaining_steps: list[PlanStep],
    ) -> list[PlanStep] | None:
        """
        评估并重规划。

        Args:
            goal: 用户的终极目标
            past_steps: 已完成步骤列表，每项为 (步骤描述, 执行结果)
            remaining_steps: 当前剩余步骤

        Returns:
            None: 目标已达成（DONE）
            list[PlanStep]: 新的剩余步骤（可能与原来相同，也可能已调整）
        """
        logger.info(f"Replanner 评估，已完成 {len(past_steps)} 步，剩余 {len(remaining_steps)} 步")

        past_text = "\n".join(
            f"- {desc}\n  结果：{result}" for desc, result in past_steps
        ) or "（无）"

        remaining_text = "\n".join(
            f"- Step {s.id}: {s.description}" for s in remaining_steps
        ) or "（无剩余步骤）"

        prompt = (
            _REPLANNER_PROMPT
            .replace("{goal}", goal)
            .replace("{past_steps}", past_text)
            .replace("{remaining_steps}", remaining_text)
        )

        try:
            response = provider.chat(
                [{"role": "user", "content": prompt}],
                tools=None,
                json_mode=True,
            )
            data = _parse_json(response.content)
        except Exception as e:
            logger.warning(f"Replanner 调用失败: {e}，保持原计划继续")
            return remaining_steps

        status = data.get("status", "continue")

        if status == "done":
            logger.info("Replanner 判断目标已达成，DONE")
            return None

        new_steps_data = data.get("steps", [])
        if not new_steps_data:
            logger.info("Replanner 返回空步骤，视为 DONE")
            return None

        new_steps = [
            PlanStep(
                id=s["id"],
                description=s["description"],
                depends_on=s.get("depends_on", []),
            )
            for s in new_steps_data
        ]

        if len(new_steps) != len(remaining_steps) or any(
            n.description != o.description for n, o in zip(new_steps, remaining_steps)
        ):
            logger.info(f"Replanner 调整了计划，新剩余步骤数: {len(new_steps)}")
        else:
            logger.info("Replanner 确认原计划不变，继续执行")

        return new_steps
