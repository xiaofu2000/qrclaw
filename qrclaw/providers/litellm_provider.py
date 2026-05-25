"""
LiteLLM Provider

统一 LLM 调用接口，支持 100+ 提供商（OpenAI/Vertex/Azure/Anthropic/DeepSeek 等）。
通过 LiteLLM 库实现，无需关注底层差异。

配置项（优先级从高到低）：
1. LITELLM_API_KEY / LITELLM_MODEL / LITELLM_BASE_URL（推荐）

"""
import litellm
from litellm import completion
from litellm.exceptions import RateLimitError, ServiceUnavailableError, APIError, APIConnectionError
from urllib.parse import urlparse

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

# Provider 前缀映射表（根据 base_url 自动推断）
PROVIDER_PREFIX_MAP = {
    "api.openai.com": "openai",
    "api minimaxi.com": "minimax",
    "api.minimaxi.com": "minimax",
    "api.deepseek.com": "deepseek",
    "api.anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "vertex_ai",
    "azure.com": "azure",
    "api.cohere.com": "cohere",
    "api.mistral.ai": "mistral",
    "api.huggingface.co": "huggingface",
    "openrouter.ai": "openrouter",
}
def _infer_provider(model: str, base_url: str | None) -> str:
    """
    推断模型对应的 provider 前缀。

    LiteLLM 要求模型格式为 provider/model-name，如 minimax/MiniMax-M2.7-highspeed
    如果模型名已包含 /，说明已有前缀，直接返回原值。
    """
    if "/" in model:
        return model  # 已有前缀

    if not base_url:
        return model  # 无法推断，返回原值

    # 从 base_url 提取 host
    parsed = urlparse(base_url if base_url.startswith("http") else f"https://{base_url}")
    host = parsed.netloc.lower()

    # 查找匹配的 provider
    for pattern, provider in PROVIDER_PREFIX_MAP.items():
        if pattern in host:
            logger.debug(f"从 base_url 推断 provider: {base_url} -> {provider}")
            return f"{provider}/{model}"

    return model  # 无法推断，返回原值
class LiteLLMProvider(LLMProvider):
    """
    LiteLLM 统一调用接口。

    支持的模型格式：
    - openai/gpt-4o
    - anthropic/claude-3-sonnet
    - vertex_ai/gemini-pro
    - deepseek/deepseek-chat
    - minimax/MiniMax-M2.7-highspeed
    - 详见 https://docs.litellm.ai/docs/providers
    """

    def __init__(self):
        self._api_key = LITELLM_API_KEY
        self._base_url = LITELLM_BASE_URL or LITELLM_API_BASE or None
        self._proxy_url = LITELLM_PROXY_URL or None

        # 自动推断 provider 前缀
        self._model = _infer_provider(LITELLM_MODEL, self._base_url)

        # LiteLLM 配置
        litellm.drop_params = True  # 忽略不支持的参数
        litellm.set_verbose = False
        litellm.ssl_verify = False  # 关闭 SSL 证书验证

        logger.info(f"LiteLLM 渠道已初始化: model={self._model}")

    @staticmethod
    def _sanitize(messages: list[dict]) -> list[dict]:
        """过滤顶层 null 字段，避免兼容性问题"""
        drop_if_null = {"refusal", "annotations", "audio", "function_call", "reasoning_content"}
        result = []
        for msg in messages:
            cleaned = {k: v for k, v in msg.items() if not (k in drop_if_null and v is None)}
            if cleaned.get("content") is None:
                cleaned["content"] = ""
            result.append(cleaned)
        return result

    @staticmethod
    def _extract_response_reasoning(message) -> str | None:
        """从 LiteLLM/OpenAI 响应里提取 reasoning content。"""
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            return reasoning
        provider_fields = getattr(message, "provider_specific_fields", None)
        if isinstance(provider_fields, dict):
            reasoning = provider_fields.get("reasoning_content")
            if reasoning:
                return reasoning
        return None

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

        # json_mode 参数保留兼容性，实际不注入 response_format
        # 原因：json_object 不被部分模型（如 MiniMax）支持，结构化输出统一走 instructor MD_JSON 模式

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
            except (RateLimitError, ServiceUnavailableError, APIConnectionError) as e:
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
        reasoning_content = self._extract_response_reasoning(message)

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

        # 提取 reasoning_content（thinking 模式模型，如 deepseek-reasoner、claude-3-7-sonnet 等）
        # 必须原样回传给下一轮，否则 API 报错
        reasoning_content = getattr(message, "reasoning_content", None)

        logger.info(f"LiteLLM 响应成功: {total_tokens} tokens")
        return LLMResponse(
            content=message.content or "",
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            raw=response,
            reasoning_content=reasoning_content,
        )

    def make_instructor_kwargs(self, messages: list[dict], temperature: float = 0.1) -> dict:
        """
        返回给 instructor 用的基础 kwargs（model、messages、api 配置）。
        调用方追加 response_model / max_retries 等参数后传给 instructor client。
        """
        kwargs: dict = {
            "model": self._model,
            "messages": self._sanitize(messages),
            "temperature": temperature,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["api_base"] = self._base_url
        return kwargs
