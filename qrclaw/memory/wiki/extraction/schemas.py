"""
Extraction Schemas - 提取相关的 Pydantic Schema

从 memory_extraction.py 迁移：
- WikiPageSchema (行 71-81)
- ConsolidatePageSchema (行 84-91)
- ExtractionSchema (行 94-101)
- ExtractionResult (行 104-106)
"""

from typing import Literal
from pydantic import BaseModel, Field


class WikiPageSchema(BaseModel):
    """单页面提取 Schema"""

    action: Literal["create", "append", "skip"]
    name: str = Field(..., description="页面名称")
    content: str = Field(default="", description="页面正文内容")
    description: str = Field(default="", description="页面描述")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    related: list[str] = Field(default_factory=list, description="关联页面")


class ConsolidatePageSchema(BaseModel):
    """页面整理 Schema（合并去重后的结果）"""

    content: str = Field(..., description="整理后的正文")
    description: str = Field(default="", description="整理后的描述")
    tags: list[str] = Field(default_factory=list, description="整理后的标签")


class ExtractionSchema(BaseModel):
    """LLM 提取结果 Schema"""

    needs_update: bool = Field(..., description="是否需要更新")
    pages: list[WikiPageSchema] = Field(default_factory=list, description="待处理页面列表")



