from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    title: str = Field(description="网页标题")
    url: str = Field(description="网页链接")
    snippet: str = Field(description="摘要内容")
    score: Optional[float] = Field(description="相关性得分", default=None)
    published_date: Optional[str] = Field(description="发布日期", default=None)


class WebSearchResponse(BaseModel):
    query: str
    provider: str
    count: int
    took_ms: Optional[float] = None
    results: List[SearchResult]
    answer: Optional[str] = Field(description="AI生成的直接答案", default=None)
    raw_response: Optional[Dict[str, Any]] = Field(description="原始API响应", default=None, exclude=True)


class WebSearchProvider(ABC):
    """
    Web 搜索 Provider 抽象基类
    所有搜索引擎插件都需要继承此类并实现 search 方法
    """
    
    @property
    @abstractmethod
    def id(self) -> str:
        """Provider ID (例如: 'tavily', 'google')"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称 (例如: 'Tavily Search')"""
        pass
        
    @abstractmethod
    def is_available(self) -> bool:
        """检查此 Provider 是否可用（例如 API Key 是否已配置）"""
        pass

    @abstractmethod
    def search(self, query: str, max_results: int = 5, **kwargs) -> WebSearchResponse:
        """执行搜索"""
        pass
