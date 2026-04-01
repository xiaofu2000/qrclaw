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
from qrclaw.memory.context.context_manager import get_context_manager
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
1. 分析战报：已完成的步骤拿到了哪些真实情报？是否遇到了致命阻塞、报错或死胡同？
2. 评估进度：终极目标是否已经彻底达成？（必须是拿到了可以直接回答用户的最终数据，才算达成）。
3. 重构图纸：如果没达成，根据最新情报重新规划后续步骤。

【重规划铁律（极重要！）】
1. 【绝对路径强制令（生死线）】：只要步骤描述中需要读取、修改或操作任何文件/目录，**必须、绝对、毫无例外地使用完整的绝对路径**！
   ❌ 严禁只写文件名或相对路径（致命错误示例：“读取 package.json”、“分析 src/main.py”）。
   ✅ 必须写明完整的绝对路径（正确示例：“读取 /绝对路径/项目/package.json”、“分析 /绝对路径/项目/src/main.py”）。
   如果战报中给出的信息不足以拼接出完整的绝对路径，你应该先规划一步去执行 `pwd` 或 `find` 获取真实绝对路径。
2. 【绝对保真与防幻觉】：必须严格使用战报中出现的真实文件。绝不允许凭空猜测路径或使用“（如 xxx.py）”这种假设性举例！
3. 【破除死循环】：如果战报显示某步骤执行失败（如文件不存在），绝对禁止在后续计划中原样重复该步骤！必须改变策略（如扩大搜索范围）。
4. 【上下文自包含】：步骤描述必须像给全新 Agent 下达的独立指令。明确写出具体要怎么做、处理哪个绝对路径的文件、目标是什么。
5. 【动态并发视野】：情报充足时，尽量规划无依赖关系的并行步骤（depends_on: []）。情报不足时，只生成 1-2 步探测任务。

【输出格式】
必须且只能输出一个纯净的 JSON 对象。绝对禁止输出 ```json 这类 Markdown 代码块标记，禁止在 JSON 外输出任何解释性文字！
必须先输出 "thought" 字段进行逻辑推演，并在推演中强制检查自己是否使用了绝对路径！

如果目标已达成，返回：
{
  "thought": "分析战报，确认所有核心目标已提取完毕并汇总，任务已彻底完成。",
  "status": "done"
}

如果需要继续执行（生成新图纸），返回：
{
  "thought": "分析战报发现... 步骤 X 报错了。我发现新线索 Y，因此调整策略。我已严格检查下方步骤，所有文件引用均已使用完整的绝对路径。",
  "status": "continue",
  "steps": [
    {"id": 1, "description": "动作描述，必须包含具体的真实绝对路径参数和明确的处理要求", "depends_on": []},
    {"id": 2, "description": "动作描述，必须包含具体的真实绝对路径参数和明确的处理要求", "depends_on": [1]}
  ]
}

注意：
- steps 里的 id 必须从 1 开始重新连续编号。
- 坚决取消原计划中已被证明无效、报错或多余的步骤。"""

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
            logger.warning("Replanner 判断目标已达成，DONE")
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
