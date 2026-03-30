"""
Router 节点

在 ReAct 循环之前，判断任务是否需要规划。
直接复用主 session 的完整消息历史 + system prompt，
LLM 在完整上下文下判断，结果最准确。

判断结果：
  - "direct"  : 简单任务，直接走 ReAct 循环
  - "plan"    : 复杂任务，先走 Planner 节点生成 Plan，再执行
"""
import json
from dataclasses import dataclass
from qrclaw.providers import provider
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.router")

# 追加在完整会话末尾的分类指令，轻量、明确
_ROUTE_QUESTION = (
    "【系统指令】根据以上对话，判断最新一条用户消息的任务是否需要制定执行计划。\n"
    "需要计划：任务含 3 个及以上步骤、涉及多个文件/模块、需先探索环境、或明确要求分阶段完成。\n"
    "不需要计划：简单问答、单文件操作、单条命令、闲聊。\n"
    "只返回 JSON，不要其他内容：\n"
    '{"route": "direct"} 或 {"route": "plan", "reason": "一句话原因"}'
)


@dataclass
class RouteResult:
    route: str          # "direct" | "plan"
    reason: str = ""    # route=plan 时说明原因，便于日志追踪


def route(
    user_input: str,
    system_prompt: str | None = None,
    history: list[dict] | None = None,
) -> RouteResult:
    """
    判断任务走直接执行还是规划节点。

    复用主 session 的 system_prompt + 完整 history，
    在末尾追加分类指令让 LLM 判断，上下文最完整，判断最准确。

    Args:
        user_input:    用户原始输入（仅用于日志）
        system_prompt: 主 agent 的 system prompt 内容
        history:       主 session 完整消息列表（含当前 user 消息）
    Returns:
        RouteResult
    """
    logger.info(f"Router 判断任务类型: {user_input[:80]}...")

    messages: list[dict] = []

    # 用主 system prompt，让 LLM 在完整身份和工具上下文下判断
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # 带入完整会话历史（已包含当前 user 消息）
    if history:
        messages.extend(history)

    # 在末尾追加分类指令
    messages.append({"role": "user", "content": _ROUTE_QUESTION})

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
        route_val = data.get("route", "direct")
        reason = data.get("reason", "")

        if route_val not in ("direct", "plan"):
            logger.warning(f"Router 返回未知值: {route_val}，降级为 direct")
            route_val = "direct"

        logger.info(f"Router 结果: {route_val}" + (f"，原因: {reason}" if reason else ""))
        return RouteResult(route=route_val, reason=reason)

    except Exception as e:
        logger.warning(f"Router 解析失败: {e}，降级为 direct")
        return RouteResult(route="direct", reason=f"router error: {e}")

