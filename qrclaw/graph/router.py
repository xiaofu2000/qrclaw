"""
Router 节点

在 ReAct 循环之前，判断任务是否需要规划。
直接复用主 session 的完整消息历史 + system prompt，
LLM 在完整上下文下判断，结果最准确。

JSON 可靠性保障（双重防御）：
  1. json_mode=True：OpenAI 协议层强制输出合法 JSON（根本解法）
  2. 正则提取兜底：Vertex AI 等不支持 json_mode 的 provider 靠正则容错

判断结果：
  - "direct"  : 简单任务，直接走 ReAct 循环
  - "plan"    : 复杂任务，先走 Planner 节点生成 Plan，再执行
"""
import json
import re
from dataclasses import dataclass
from qrclaw.providers import provider
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.router")

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
    reason: str = ""


def _parse_json(raw: str) -> dict:
    """
    从 LLM 输出中提取 JSON，双重容错：
    1. 直接解析（json_mode 下 LLM 保证输出合法 JSON）
    2. 正则提取第一个 {...}（Vertex AI 等不支持 json_mode 时的兜底）
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 正则提取第一个 JSON 对象，容忍 LLM 在 JSON 前后加废话
    match = re.search(r'\{.*?\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"无法从输出中提取 JSON: {raw[:100]}")


def route(
    user_input: str,
    system_prompt: str | None = None,
    history: list[dict] | None = None,
) -> RouteResult:
    """
    判断任务走直接执行还是规划节点。

    Args:
        user_input:    用户原始输入（仅用于日志）
        system_prompt: 主 agent 的 system prompt 内容
        history:       主 session 完整消息列表（含当前 user 消息）
    Returns:
        RouteResult
    """
    logger.info(f"Router 判断任务类型: {user_input[:80]}...")

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": _ROUTE_QUESTION})

    try:
        # json_mode=True：OpenAI 协议层保证输出合法 JSON
        # Vertex AI 不支持此参数，忽略，靠 _parse_json 正则兜底
        response = provider.chat(messages, tools=None, json_mode=True)
        data = _parse_json(response.content)

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


