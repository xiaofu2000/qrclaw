"""
LLM service boundary.

New orchestration code should depend on this service instead of importing the
global provider directly. Existing modules can migrate gradually.
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from qrclaw.providers.base import LLMProvider, LLMResponse

T = TypeVar("T")


class LLMService:
    """Thin adapter around the configured provider."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        # 所有调用（含 Wiki 提取、选页和摘要）都经过同一输入上限校验。
        from qrclaw.memory.token_utils import check_input_budget
        check_input_budget(messages, tools)
        return self.provider.chat(
            messages=messages,
            tools=tools,
            json_mode=json_mode,
            temperature=temperature,
            on_delta=on_delta,
        )

    def structured(
        self,
        messages: list[dict],
        response_model: type[T],
        temperature: float = 0.1,
        max_retries: int = 3,
    ) -> T:
        """Call the provider through instructor and parse a structured model."""
        import instructor

        client = instructor.patch(
            create=self.create_openai_like,
            mode=instructor.Mode.MD_JSON,
        )
        return client(
            messages=messages,
            response_model=response_model,
            temperature=temperature,
            max_retries=max_retries,
        )

    def create_openai_like(self, messages: list[dict], **kwargs: Any):
        """Adapter used by instructor.patch(create=...)."""
        response = self.chat(
            messages=messages,
            temperature=kwargs.get("temperature"),
        )
        return _to_openai_like_response(response, model_name=self.model_name)

    @property
    def model_name(self) -> str:
        return str(getattr(self.provider, "_model", "") or "unknown")

    def make_instructor_kwargs(self, messages: list[dict], temperature: float = 0.1) -> dict:
        """Compatibility bridge for providers that expose instructor kwargs."""
        from qrclaw.memory.token_utils import check_input_budget
        check_input_budget(messages)
        if not hasattr(self.provider, "make_instructor_kwargs"):
            raise RuntimeError("当前 provider 不支持 instructor 结构化调用")
        return self.provider.make_instructor_kwargs(messages, temperature=temperature)

    def is_provider_type(self, provider_type: type) -> bool:
        return isinstance(self.provider, provider_type)


class _MockChoice:
    def __init__(self, content: str):
        self.message = type("Message", (), {"content": content})()
        self.finish_reason = "stop"
        self.index = 0


class _MockUsage:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0


class _MockOpenAIResponse:
    def __init__(self, content: str, model: str):
        self.choices = [_MockChoice(content)]
        self.model = model
        self.usage = _MockUsage()


def _to_openai_like_response(response: LLMResponse, model_name: str):
    return _MockOpenAIResponse(response.content or "", model_name)


def get_llm_service() -> LLMService:
    from qrclaw.providers import provider

    return LLMService(provider)
