"""验证 OrcaRouter 配置经过真实 Provider 后得到正确的请求参数。"""
from types import SimpleNamespace
from unittest.mock import Mock

import litellm
import pytest

from qrclaw.providers import litellm_provider as provider_module


@pytest.mark.parametrize("configured_model,base_url,expected_model,wire_model", [
    ("orcarouter/fusion", "https://api.orcarouter.ai/v1", "openai/orcarouter/fusion", "orcarouter/fusion"),
    ("fusion", "https://api.orcarouter.ai/v1", "openai/orcarouter/fusion", "orcarouter/fusion"),
    ("fusion-mini", "https://api.orcarouter.ai/v1", "openai/orcarouter/fusion-mini", "orcarouter/fusion-mini"),
    ("orcarouter/auto", "https://api.orcarouter.ai/v1", "openai/orcarouter/auto", "orcarouter/auto"),
    ("openai/orcarouter/fusion", "https://api.orcarouter.ai/v1", "openai/orcarouter/fusion", "orcarouter/fusion"),
    ("gpt-4o", "https://api.openai.com/v1", "openai/gpt-4o", "gpt-4o"),
    ("openai/gpt-4o", "https://example.test/v1", "openai/gpt-4o", "gpt-4o"),
])
def test_provider_model_and_key_routing(monkeypatch, configured_model, base_url, expected_model, wire_model):
    """短模型名、带前缀模型名和既有渠道都应保留正确的模型、端点及密钥。"""
    monkeypatch.setattr(provider_module, "LITELLM_MODEL", configured_model)
    monkeypatch.setattr(provider_module, "LITELLM_BASE_URL", base_url)
    monkeypatch.setattr(provider_module, "LITELLM_API_KEY", "test-orca-key")
    monkeypatch.setattr(provider_module, "LITELLM_PROXY_URL", "")
    response = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="完成", tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    completion = Mock(return_value=response)
    monkeypatch.setattr(provider_module, "completion", completion)
    provider = provider_module.LiteLLMProvider()
    assert provider.chat([{"role": "user", "content": "测试"}]).content == "完成"
    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == expected_model
    assert kwargs["api_base"] == base_url
    assert kwargs["api_key"] == "test-orca-key"
    structured = provider.make_instructor_kwargs([{"role": "user", "content": "结构化请求"}])
    assert (structured["model"], structured["api_base"], structured["api_key"]) == (expected_model, base_url, "test-orca-key")
    actual_model, actual_provider, _, _ = litellm.get_llm_provider(model=expected_model, api_base=base_url, api_key="test-orca-key")
    assert (actual_model, actual_provider) == (wire_model, "openai")
