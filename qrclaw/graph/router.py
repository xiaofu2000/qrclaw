"""
Router 节点

在 ReAct 循环之前，用一次轻量 LLM 调用判断任务是否需要规划。
不带工具列表，只返回结构化判断，token 消耗极少。

判断结果：
  - "direct"  : 简单任务，直接走 ReAct 循环
  - "plan"    : 复杂任务，先走 Planner 节点生成 Plan，再执行

上下文传递：
  把主 session 最近 N 条历史消息带入，Router 能感知多轮对话上下文，
  避免"然后再帮我写测试"这类指代上文的请求被误判。
"""
import json
from dataclasses import dataclass
from qrclaw.providers import provider
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.router")

# 带入的历史消息条数，只取最近几条保持轻量
_HISTORY_WINDOW = 6

# Router 的 system prompt，极简，只做分类
_ROUTER_SYSTEM = """判断用户最新一条消息的任务是否需要制定执行计划。
如果有历史对话，结合上下文理解用户意图再判断。

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


def route(user_input: str, history: list[dict] | None = None) -> RouteResult:
    """
    判断任务走直接执行还是规划节点。

    Args:
        user_input: 用户原始输入
        history:    主 session 的历史消息列表，传入后取最近 _HISTORY_WINDOW 条
                    用于感知多轮对话上下文，避免指代上文的请求被误判
    Returns:
        RouteResult
    """
    logger.info(f"Router 判断任务类型: {user_input[:80]}...")

    # 取最近 N 条历史（过滤掉 tool 消息，只保留 user/assistant，减少噪音）
    context_msgs: list[dict] = []
    if history:
        recent = [
            m for m in history
            if m.get("role") in ("user", "assistant")
        ][-_HISTORY_WINDOW:]
        # 只保留 role 和 content，去掉 tool_calls 等字段，保持轻量
        context_msgs = [
            {"role": m["role"], "content": m.get("content") or ""}
            for m in recent
        ]

    messages = [
        {"role": "system", "content": _ROUTER_SYSTEM},
        *context_msgs,
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
