"""
LiteLLM Provider

统一 LLM 调用接口，支持 100+ 提供商（OpenAI/Vertex/Azure/Anthropic/DeepSeek 等）。
通过 LiteLLM 库实现，无需关注底层差异。

配置项（优先级从高到低）：
1. LITELLM_API_KEY / LITELLM_MODEL / LITELLM_BASE_URL（推荐）
2. OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL（兼容性别名）
"""
import litellm
from litellm import completion
from litellm.exceptions import RateLimitError, ServiceUnavailableError, APIError

from qrclaw.providers.base import LLMProvider, LLMResponse, ToolCall
from qrclaw.config import (
    LITELLM_API_KEY,
    LITELLM_MODEL,
    LITELLM_BASE_URL,
    LITELLM_API_BASE,
    LITELLM_PROXY_URL,
)
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.providers.litellm")


class LiteLLMProvider(LLMProvider):
    """
    LiteLLM 统一调用接口。

    支持的模型格式：
    - openai/gpt-4o
    - anthropic/claude-3-sonnet
    - vertex_ai/gemini-pro
    - deepseek/deepseek-chat
    - 详见 https://docs.litellm.ai/docs/providers
    """

    def __init__(self):
        self._api_key = LITELLM_API_KEY
        self._model = LITELLM_MODEL
        self._base_url = LITELLM_BASE_URL or LITELLM_API_BASE or None
        self._proxy_url = LITELLM_PROXY_URL or None

        # LiteLLM 配置
        litellm.drop_params = True  # 忽略不支持的参数
        litellm.set_verbose = False

        logger.info(f"LiteLLM 渠道已初始化: model={self._model}")

    @staticmethod
    def _sanitize(messages: list[dict]) -> list[dict]:
        """过滤顶层 null 字段，避免兼容性问题"""
        drop_if_null = {"refusal", "annotations", "audio", "function_call"}
        result = []
        for msg in messages:
            cleaned = {k: v for k, v in msg.items() if not (k in drop_if_null and v is None)}
            if cleaned.get("content") is None:
                cleaned["content"] = ""
            result.append(cleaned)
        return result

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> LLMResponse:
        """
        发送消息，返回统一格式的响应。

        Args:
            messages:    消息列表
            tools:       工具 schema 列表
            json_mode:   为 True 时强制 LLM 输出合法 JSON
            temperature: 温度参数，越低越确定性输出
        """
        kwargs = {
            "model": self._model,
            "messages": self._sanitize(messages),
        }

        # API 配置
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["api_base"] = self._base_url
        if self._proxy_url:
            kwargs["proxy"] = self._proxy_url

        # 工具调用
        if tools:
            kwargs["tools"] = tools

        # JSON 模式
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        # 温度参数
        if temperature is not None:
            kwargs["temperature"] = temperature

        # 重试逻辑
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                response = completion(**kwargs)
                break
            except (RateLimitError, ServiceUnavailableError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"LiteLLM 请求失败（{type(e).__name__}），第 {attempt + 1} 次重试，等待 {wait}s...")
                    import time
                    time.sleep(wait)
            except APIError as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"LiteLLM API 错误（{e.status_code}），第 {attempt + 1} 次重试，等待 {wait}s...")
                    import time
                    time.sleep(wait)
            except Exception as e:
                # 未知异常直接抛出，不使用 break（避免 UnboundLocalError）
                raise RuntimeError(f"LiteLLM 调用失败: {e}") from e
        else:
            raise RuntimeError(f"LiteLLM 连续 {max_retries} 次失败: {last_error}")

        # 解析响应
        choice = response.choices[0]
        message = choice.message

        # 提取工具调用
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                ))

        # finish_reason 修正
        finish_reason = choice.finish_reason
        if tool_calls and finish_reason not in ("tool_calls", "stop"):
            finish_reason = "tool_calls"

        # 提取 usage
        usage = getattr(response, "usage", None) or {}
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or 0

        logger.info(f"LiteLLM 响应成功: {total_tokens} tokens")
        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            raw=response,
        )
