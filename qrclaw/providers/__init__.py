from qrclaw.providers.base import LLMProvider, LLMResponse, ToolCall
from qrclaw.providers.litellm_provider import LiteLLMProvider
from qrclaw.config import LLM_PROVIDER
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.providers")

_REGISTRY = {
    "litellm": "qrclaw.providers.litellm_provider.LiteLLMProvider",
}


def _load_provider() -> LLMProvider:
    path = _REGISTRY.get(LLM_PROVIDER)
    if not path:
        raise ValueError(f"未知的 LLM 渠道: {LLM_PROVIDER}，可选: {list(_REGISTRY.keys())}")

    module_path, class_name = path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    logger.info(f"加载 LLM 渠道: {LLM_PROVIDER}")
    return cls()


def reload_provider() -> LLMProvider:
    """根据磁盘中的最新设置重建全局 Provider。"""

    global provider

    from qrclaw import config_manager

    config = config_manager.get_config()
    llm = config.get("llm", {})
    provider_name = llm.get("provider", "litellm")
    if provider_name != "litellm":
        raise ValueError(f"不支持的模型渠道: {provider_name}")
    provider = LiteLLMProvider(
        api_key=str(llm.get("api_key", "")),
        model=str(llm.get("model", "")),
        base_url=str(llm.get("base_url", "")),
        proxy_url=str(llm.get("proxy_url", "")),
    )
    logger.info("模型渠道配置已重新加载")
    return provider


provider: LLMProvider = _load_provider()
