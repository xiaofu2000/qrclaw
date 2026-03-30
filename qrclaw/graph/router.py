"""
Router 节点

在 ReAct 循环之前，用一次轻量 LLM 调用判断任务是否需要规划。
不带工具列表，只返回结构化判断，token 消耗极少。

判断结果：
  - "direct"  : 简单任务，直接走 ReAct 循环
  - "plan"    : 复杂任务，先走 Planner 节点生成 Plan，再执行
"""
import json
from dataclasses import dataclass
from qrclaw.providers import provider
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.router")

# Router 的 system prompt，极简，只做分类
_ROUTER_SYSTEM = """你是一个任务分类器，判断用户的任务是否需要制定执行计划。

需要制定计划的情况（返回 "plan"）：
1. 任务包含 3 个及以上明确的步骤
2. 任务涉及多个文件、模块或方向，可以并行处理
3. 任务需要先探索环境再决定后续步骤
4. 任务明确要求分阶段完成

不需要计划（返回 "direct"）：
1. 简单问答、解释、翻译
2. 单个文件操作
3. 单条命令执行
4. 闲聊

只返回 JSON，格式：
{"route": "direct"} 或 {"route": "plan", "reason": "一句话说明原因"}

不要返回任何其他内容。"""


@dataclass
class RouteResult:
    route: str          # "direct" | "plan"
    reason: str = ""    # route=plan 时说明原因，便于日志追踪


def route(user_input: str) -> RouteResult:
    """
    判断任务走直接执行还是规划节点。

    Args:
        user_input: 用户原始输入
    Returns:
        RouteResult
    """
    logger.info(f"Router 判断任务类型: {user_input[:80]}...")

    messages = [
        {"role": "system", "content": _ROUTER_SYSTEM},
        {"role": "user", "content": user_input},
    ]

    try:
        # 不传 tools，纯文本输出，消耗 token 极少
        response = provider.chat(messages, tools=None)
        raw = response.content.strip()

        # 尝试从 markdown 代码块里提取 JSON
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data = json.loads(raw)
        route_val = data.get("route", "direct")
        reason = data.get("reason", "")

        if route_val not in ("direct", "plan"):
            logger.warning(f"Router 返回未知值: {route_val}，降级为 direct")
            route_val = "direct"

        logger.info(f"Router 结果: {route_val}" + (f"，原因: {reason}" if reason else ""))
        return RouteResult(route=route_val, reason=reason)

    except Exception as e:
        # 解析失败降级为 direct，保证主流程不中断
        logger.warning(f"Router 解析失败: {e}，降级为 direct")
        return RouteResult(route="direct", reason=f"router error: {e}")
