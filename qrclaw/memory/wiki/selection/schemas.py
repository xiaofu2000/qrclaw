"""
Selection Schemas - Wiki Page 智能选择器的 Pydantic Schema

从 extraction/schemas.py 模式参考设计
"""

from pydantic import BaseModel, Field


class SelectedPage(BaseModel):
    """被选中的页面"""

    name: str = Field(..., description="页面名称")
    reason: str = Field(default="", description="选择理由")
    relevance: float = Field(default=0.0, ge=0.0, le=1.0, description="相关性得分 0-1")


class SelectionResult(BaseModel):
    """选择结果 Schema"""

    selected: list[SelectedPage] = Field(
        default_factory=list, description="选中的页面列表"
    )
    reasoning: str = Field(
        default="", description="整体选择思路（简短一句话）"
    )
