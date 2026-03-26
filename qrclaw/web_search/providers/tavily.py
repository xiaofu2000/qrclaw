import os
import time
import requests
from typing import List, Optional, Dict, Any
from qrclaw.web_search.types import WebSearchProvider, SearchResult, WebSearchResponse
from qrclaw.config import TAVILY_API_KEY
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.web_search.providers.tavily")

class TavilySearchProvider(WebSearchProvider):
    @property
    def id(self) -> str:
        return "tavily"

    @property
    def name(self) -> str:
        return "Tavily Search"

    def is_available(self) -> bool:
        return bool(TAVILY_API_KEY)

    def search(self, query: str, max_results: int = 5, **kwargs) -> WebSearchResponse:
        """
        执行 Tavily 搜索
        
        Args:
            query: 搜索词
            max_results: 返回结果数量 (1-20)
            **kwargs: 其他参数，如 search_depth, include_answer
        """
        if not self.is_available():
            raise ValueError("TAVILY_API_KEY is not configured")
            
        start_time = time.time()
        
        # 参数处理
        count = max(1, min(20, int(max_results)))
        search_depth = kwargs.get("search_depth", "basic")
        include_answer = kwargs.get("include_answer", False)
        
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "max_results": count,
            "search_depth": search_depth,
            "include_answer": include_answer,
            "include_images": False,
            "include_raw_content": False
        }
        
        try:
            logger.debug(f"Tavily search request: {query} (count={count})")
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            results: List[SearchResult] = []
            raw_results = data.get("results", [])
            
            for item in raw_results:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", "")[:500],  # 限制摘要长度
                    score=item.get("score"),
                    published_date=item.get("published_date")
                ))
            
            took_ms = (time.time() - start_time) * 1000
            
            return WebSearchResponse(
                query=query,
                provider=self.id,
                count=len(results),
                took_ms=took_ms,
                results=results,
                answer=data.get("answer"),
                raw_response=data
            )
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Tavily search failed: {e}")
            raise RuntimeError(f"Tavily API error: {e}")
