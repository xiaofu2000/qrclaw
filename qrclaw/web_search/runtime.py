import os
import time
from typing import Optional, Dict, Any, List
from qrclaw.web_search.types import WebSearchResponse, SearchResult, WebSearchProvider
from qrclaw.web_search.provider_registry import list_providers, get_provider
from qrclaw.config import TAVILY_API_KEY
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.web_search.runtime")

class WebSearchError(Exception):
    """Web Search Runtime Error"""
    pass

def resolve_web_search_provider_id(prefer_provider: Optional[str] = None) -> Optional[str]:
    """
    确定使用哪个 Provider。
    优先级逻辑：
    1. 用户显式指定 (prefer_provider)
    2. Tavily (最强，如果有 Key)
    3. Google (次强，如果有 Key)
    4. DuckDuckGo (免费保底)
    """
    if prefer_provider:
        p_cls = get_provider(prefer_provider)
        if p_cls and p_cls().is_available():
            return prefer_provider
            
    # 按优先级列表自动探测
    # 这里我们硬编码一个优先级顺序，而不是简单遍历注册表
    priority_order = ["tavily", "google", "duckduckgo"]
    
    for pid in priority_order:
        p_cls = get_provider(pid)
        if p_cls:
            provider = p_cls()
            if provider.is_available():
                logger.info(f"Auto-detected web search provider: {provider.name}")
                return provider.id
            
    # 如果上面的都没选中（理论上 DuckDuckGo 总是可用），再尝试剩下的
    for p_cls in list_providers():
        provider = p_cls()
        if provider.is_available():
             # 避免重复选
            if provider.id not in priority_order:
                logger.info(f"Auto-detected fallback provider: {provider.name}")
                return provider.id

    return None

def run_web_search(query: str, max_results: int = 5, provider_id: Optional[str] = None, **kwargs) -> WebSearchResponse:
    """
    执行 Web 搜索
    """
    if provider_id:
        provider_ids = [provider_id]
    else:
        provider_ids = []
        selected_id = resolve_web_search_provider_id()
        if selected_id:
            provider_ids.append(selected_id)
            for candidate in ["tavily", "google", "duckduckgo"]:
                if candidate not in provider_ids:
                    provider_ids.append(candidate)
        else:
            provider_ids = ["tavily", "google", "duckduckgo"]

    tried: list[str] = []
    last_error: Exception | None = None

    for pid in provider_ids:
        provider_cls = get_provider(pid)
        if not provider_cls:
            continue

        provider = provider_cls()
        if not provider.is_available():
            continue

        tried.append(provider.id)
        logger.info(f"Executing web search with provider: {provider.name}")

        try:
            return provider.search(query=query, max_results=max_results, **kwargs)
        except Exception as e:
            last_error = e
            logger.error(f"Web search failed with {provider.name}: {e}")
            continue

    fallback_error = f"No web search provider succeeded. Tried: {tried or provider_ids}"
    if last_error:
        raise WebSearchError(f"{fallback_error}; last error: {last_error}") from last_error
    raise WebSearchError(fallback_error)
