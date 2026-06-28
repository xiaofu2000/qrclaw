"""Message serialization helpers for graph nodes."""
from __future__ import annotations

from qrclaw.providers.base import LLMResponse


def assistant_response_to_message(response: LLMResponse) -> dict:
    """Convert an LLM response into replay-safe assistant message data."""
    msg: dict = {"role": "assistant", "content": response.content or ""}
    if response.reasoning_content:
        msg["reasoning_content"] = response.reasoning_content
    if response.tool_calls:
        tool_calls = []
        for tool_call in response.tool_calls:
            entry = {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                },
            }
            if tool_call.thought_signature:
                entry["__thought_signature__"] = tool_call.thought_signature
            tool_calls.append(entry)
        msg["tool_calls"] = tool_calls
    return msg


def tool_result_to_message(tool_call_id: str, content: str) -> dict:
    """Build a tool-result message in the format expected by providers."""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

