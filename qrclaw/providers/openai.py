from openai import OpenAI
from qrclaw.providers.base import LLMProvider, LLMResponse, ToolCall
from qrclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.providers.openai")


class OpenAIProvider(LLMProvider):

    def __init__(self):
        self._client = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL or None,
        )
        logger.info("OpenAI 渠道已初始化")

    @staticmethod
    def _sanitize(messages: list[dict]) -> list[dict]:
        """过滤顶层 null 字段，避免 Gemini 兼容层报 400"""
        drop_if_null = {"refusal", "annotations", "audio", "function_call"}
        result = []
        for msg in messages:
            cleaned = {k: v for k, v in msg.items() if not (k in drop_if_null and v is None)}
            if cleaned.get("content") is None:
                cleaned["content"] = ""
            result.append(cleaned)
        return result

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        kwargs = {"model": OPENAI_MODEL, "messages": self._sanitize(messages)}
        if tools:
            kwargs["tools"] = tools

        response = self._client.chat.completions.create(**kwargs)
        usage = response.usage
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                ))

        finish_reason = choice.finish_reason
        if tool_calls and finish_reason not in ("tool_calls", "stop"):
            finish_reason = "tool_calls"

        logger.info(f"LLM 响应成功，使用 {usage.total_tokens} tokens")
        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            raw=response,
        )
