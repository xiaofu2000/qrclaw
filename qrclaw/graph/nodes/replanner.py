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
from qrclaw.memory.step_result import StepResult
from qrclaw.memory.context_manager import get_context_manager
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.replanner")

_REPLANNER_PROMPT = """你是一个高级智能体任务重规划器 (Replanner)。你需要根据前线传回的真实战报，评估当前的战略进度，并决定是继续按原计划执行、调整计划，还是宣布胜利。

【终极目标】
{goal}

【已完成步骤及真实结果 (战报)】
{past_steps}

【原定剩余步骤 (旧图纸)】
{remaining_steps}

【你的核心任务】
1. 分析战报：已完成的步骤拿到了哪些真实情报？是否遇到了致命阻塞？
2. 评估进度：终极目标是否已经彻底达成？（不要敷衍，只有真正拿到最终结果才算达成）。
3. 重构图纸：如果没达成，你需要根据最新情报重新规划后续步骤。可以全盘保留原计划、部分修改，或者完全推翻重来。

【重规划铁律（极重要！）】
1. 【绝对保真】：在规划下一步读取/操作具体文件时，必须严格使用【战报】中出现的真实文件名或路径。绝对禁止猜测、编造或使用“（如 xxx.py）”这样的举例！
2. 【上下文注入】：步骤描述必须自包含。不要写“处理上一步的文件”，而是明确写出“分析战报中提取的 /xxx/yyy.py 文件”。
3. 【并发最大化】：在情报充足的情况下，尽量规划无依赖关系的并行步骤（depends_on: []）。如果情报不足以规划全局，允许只生成 1-2 步探测任务。

【输出格式】
严格输出 JSON，禁止包含 Markdown 标记或额外解释。必须先输出 "thought" 字段进行逻辑推演！

如果目标已达成，返回：
{
  "thought": "分析战报，确认所有核心目标已提取完毕，任务已完成。",
  "status": "done"
}

如果需要继续执行（生成新图纸），返回：
{
  "thought": "分析战报发现... 原定计划中的步骤 X 已经不需要了，但我发现了一个新线索 Y，因此我需要规划 3 个新步骤：步骤 1 去看 A，步骤 2 去看 B，步骤 3 汇总...",
  "status": "continue",
  "steps": [
    {"id": 1, "description": "步骤描述，包含具体的真实路径参数", "depends_on": []},
    {"id": 2, "description": "步骤描述...", "depends_on": [1]}
  ]
}

注意：
- steps 里的 id 必须从 1 开始重新连续编号。
- 取消原定计划中已经被证明无效或错误的步骤。"""


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    # 剥掉 <think>...</think> 标签（MiniMax 等 thinking 模型会返回）
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"无法解析 Replanner 输出: {raw[:100]}")


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

        messages = ctx.build_messages("replanner")

        try:
            response = provider.chat(
                messages,
                tools=None,
                json_mode=True,
            )
            data = _parse_json(response.content)
        except Exception as e:
            logger.warning(f"Replanner 调用失败: {e}，保持原计划继续")
            return ctx.plan_state.remaining

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

        if len(new_steps) != len(ps.remaining) or any(
            n.description != o.description for n, o in zip(new_steps, ps.remaining)
        ):
            logger.info(f"Replanner 调整了计划，新剩余步骤数: {len(new_steps)}")
        else:
            logger.info("Replanner 确认原计划不变，继续执行")

        return new_steps
