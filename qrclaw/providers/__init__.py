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


provider: LLMProvider = _load_provider()
