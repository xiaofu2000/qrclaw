import time
from typing import List
from ddgs import DDGS
from qrclaw.web_search.types import WebSearchProvider, SearchResult, WebSearchResponse
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.web_search.providers.duckduckgo")

class DuckDuckGoSearchProvider(WebSearchProvider):
    @property
    def id(self) -> str:
        return "duckduckgo"

    @property
    def name(self) -> str:
        return "DuckDuckGo Search"

    def is_available(self) -> bool:
        # 始终可用，不需要 Key
        return True

    def search(self, query: str, max_results: int = 5, **kwargs) -> WebSearchResponse:
        start_time = time.time()
        logger.debug(f"DuckDuckGo searching: {query}")

        results: List[SearchResult] = []
        try:
            with DDGS() as ddgs:
                # region="wt-wt" 表示全球搜索，timelimit="y" 表示最近一年(可选)
                ddg_results = ddgs.text(query, max_results=max_results, region="wt-wt")

                for r in ddg_results:
                    results.append(SearchResult(
                        title=r.get("title", ""),
                        url=r.get("href", ""),
                        snippet=r.get("body", "")
                    ))
        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            raise RuntimeError(f"DuckDuckGo API error: {e}")

        took_ms = (time.time() - start_time) * 1000
        return WebSearchResponse(
            query=query,
            provider=self.id,
            count=len(results),
            took_ms=took_ms,
            results=results
        )
