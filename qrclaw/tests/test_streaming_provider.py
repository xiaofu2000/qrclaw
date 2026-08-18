"""LiteLLM 流式文本和工具调用合并测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from qrclaw.providers.litellm_provider import LiteLLMProvider


def _chunk(
    *,
    content: str = "",
    finish_reason=None,
    tool_calls=None,
    usage=None,
):
    """构造最小 LiteLLM 流式分片。"""

    delta = SimpleNamespace(
        content=content,
        reasoning_content=None,
        tool_calls=tool_calls or [],
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_streaming_text_calls_delta_callback():
    """文本分片应实时回调并合并为最终响应。"""

    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)
    chunks = [
        _chunk(content="你"),
        _chunk(content="好", finish_reason="stop", usage=usage),
    ]
    deltas = []

    with patch("qrclaw.providers.litellm_provider.completion", return_value=iter(chunks)):
        response = LiteLLMProvider().chat(
            [{"role": "user", "content": "你好"}],
            on_delta=deltas.append,
        )

    assert deltas == ["你", "好"]
    assert response.content == "你好"
    assert response.finish_reason == "stop"
    assert response.total_tokens == 5


def test_streaming_tool_call_merges_fragments():
    """工具名称和参数分片应合并为一次完整调用。"""

    first_tool = SimpleNamespace(
        index=0,
        id="call_1",
        function=SimpleNamespace(name="run_", arguments='{"command":"'),
    )
    second_tool = SimpleNamespace(
        index=0,
        id=None,
        function=SimpleNamespace(name="shell", arguments='echo test"}'),
    )
    chunks = [
        _chunk(tool_calls=[first_tool]),
        _chunk(tool_calls=[second_tool], finish_reason="tool_calls"),
    ]

    with patch("qrclaw.providers.litellm_provider.completion", return_value=iter(chunks)):
        response = LiteLLMProvider().chat(
            [{"role": "user", "content": "执行命令"}],
            tools=[{"type": "function"}],
            on_delta=lambda _: None,
        )

    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "run_shell"
    assert response.tool_calls[0].arguments == '{"command":"echo test"}'
