from typing import Dict, List, Optional, Type
from qrclaw.web_search.types import WebSearchProvider
from qrclaw.web_search.providers.tavily import TavilySearchProvider
from qrclaw.web_search.providers.duckduckgo import DuckDuckGoSearchProvider
from qrclaw.web_search.providers.google import GoogleSearchProvider

_providers: Dict[str, Type[WebSearchProvider]] = {}

def register_provider(provider_cls: Type[WebSearchProvider]):
    """注册一个新的 Search Provider"""
    _providers[provider_cls().id] = provider_cls

def list_providers() -> List[Type[WebSearchProvider]]:
    """列出所有已注册的 Provider"""
    return list(_providers.values())

def get_provider(provider_id: str) -> Optional[Type[WebSearchProvider]]:
    """根据 ID 获取 Provider"""
    return _providers.get(provider_id)

# 注册所有 Provider
# 注意：Runtime 的自动选择逻辑会按照这里的注册顺序或者显式优先级来挑
register_provider(TavilySearchProvider)
register_provider(GoogleSearchProvider)
register_provider(DuckDuckGoSearchProvider)
