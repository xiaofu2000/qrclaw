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
    selected_id = provider_id or resolve_web_search_provider_id()
    
    if not selected_id:
        error_msg = "No web search provider is available."
        logger.error(error_msg)
        raise WebSearchError(error_msg)
        
    provider_cls = get_provider(selected_id)
    if not provider_cls:
        raise WebSearchError(f"Provider '{selected_id}' not found.")
        
    provider = provider_cls()
    logger.info(f"Executing web search with provider: {provider.name}")
    
    try:
        return provider.search(query=query, max_results=max_results, **kwargs)
    except Exception as e:
        logger.error(f"Web search failed with {provider.name}: {e}")
        
        # 简单的故障转移逻辑：如果首选失败，尝试降级到 DuckDuckGo
        if provider.id != "duckduckgo":
            logger.warning("Attempting fallback to DuckDuckGo...")
            fallback_cls = get_provider("duckduckgo")
            if fallback_cls:
                fallback = fallback_cls()
                return fallback.search(query=query, max_results=max_results, **kwargs)
        
        raise
