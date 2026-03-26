import os
import time
from typing import List
from googleapiclient.discovery import build
from qrclaw.web_search.types import WebSearchProvider, SearchResult, WebSearchResponse
from qrclaw.logger import get_logger

# 环境变量：GOOGLE_API_KEY 和 GOOGLE_CSE_ID
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")

logger = get_logger("qrclaw.web_search.providers.google")

class GoogleSearchProvider(WebSearchProvider):
    @property
    def id(self) -> str:
        return "google"

    @property
    def name(self) -> str:
        return "Google Search"

    def is_available(self) -> bool:
        return bool(GOOGLE_API_KEY and GOOGLE_CSE_ID)

    def search(self, query: str, max_results: int = 5, **kwargs) -> WebSearchResponse:
        if not self.is_available():
            raise RuntimeError("GOOGLE_API_KEY or GOOGLE_CSE_ID not configured")

        start_time = time.time()
        logger.debug(f"Google searching: {query}")
        
        try:
            service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
            # max results per page is 10
            num = max(1, min(10, int(max_results)))
            
            res = service.cse().list(
                q=query,
                cx=GOOGLE_CSE_ID,
                num=num
            ).execute()
            
            items = res.get("items", [])
            results: List[SearchResult] = []
            
            for item in items:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", "")
                ))

        except Exception as e:
            logger.error(f"Google search failed: {e}")
            raise RuntimeError(f"Google API error: {e}")

        took_ms = (time.time() - start_time) * 1000
        return WebSearchResponse(
            query=query,
            provider=self.id,
            count=len(results),
            took_ms=took_ms,
            results=results
        )
