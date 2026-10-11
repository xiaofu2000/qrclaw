"""
MessageBuilder —— 角色消息组装器。
"""
from __future__ import annotations

from qrclaw.prompts import ROUTER_SYSTEM_PROMPT, REPLANNER_PROMPT_TEMPLATE


def build_react_messages(system_prompt: str, session_messages: list[dict]) -> list[dict]:
    return [{"role": "system", "content": system_prompt}, *session_messages]


def build_router_messages(session_messages: list[dict], route_instruction: str) -> list[dict]:
    filtered = _filter_for_router(session_messages)
    messages = [{"role": "system", "content": ROUTER_SYSTEM_PROMPT}, *filtered]
    messages.append({"role": "user", "content": route_instruction})
    return messages


def build_replanner_messages(
    goal: str,
    project_path: str,
    past_steps_text: str,
    remaining_steps_text: str,
    replanner_instruction: str = "",
) -> list[dict]:
    prompt = REPLANNER_PROMPT_TEMPLATE.format(
        goal=goal,
        project_path=project_path or "（未指定，请从战报中推断）",
        past_steps=past_steps_text or "（无）",
        remaining_steps=remaining_steps_text or "（无剩余步骤）",
    )
    messages = [{"role": "user", "content": prompt}]
    if replanner_instruction:
        messages.append({"role": "user", "content": replanner_instruction})
    return messages


def _filter_for_router(messages: list[dict]) -> list[dict]:
    filtered = []
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            filtered.append(msg)
        elif role == "assistant" and not msg.get("tool_calls"):
            filtered.append(msg)
    return filtered
