"""评测数据集与评测结果报告的结构化 Schema 定义。"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

EvalCategoryName = Literal[
    "memory_extraction",
    "memory_provenance_and_retrieval",
    "context_compression",
    "agent_routing_and_tools",
]

EvalMode = Literal["offline", "live"]


class EvalCaseSpec(BaseModel):
    """单条评测用例配置，保留各场景专属字段供执行器读取。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=3, pattern=r"^[a-z0-9_]+$", description="全局唯一用例 ID")
    title: str = Field(..., min_length=4, description="中文场景标题")
    scenario: str = Field(..., min_length=3, description="对应的评测执行器场景标识")
    weight: float = Field(default=1.0, gt=0.0, le=5.0, description="加权计分权重")
    description: str = Field(..., min_length=6, description="场景评估目标说明")


class CategoryDataset(BaseModel):
    """单个维度的评测数据集文件结构。"""

    category: EvalCategoryName
    title: str = Field(..., min_length=2)
    description: str = Field(..., min_length=6)
    cases: list[EvalCaseSpec] = Field(..., min_length=1)


class CheckItem(BaseModel):
    """用例内部单项断言或连续指标检查结果。"""

    name: str
    passed: bool
    score: float = Field(..., ge=0.0, le=1.0)
    weight: float = Field(default=1.0, gt=0.0)
    detail: str = ""


class CaseReport(BaseModel):
    """单条用例执行与评分汇总。"""

    id: str
    category: EvalCategoryName
    title: str
    scenario: str
    weight: float
    passed: bool
    score: float = Field(..., ge=0.0, le=1.0)
    duration_ms: float
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    checks: list[CheckItem] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class CategorySummary(BaseModel):
    """按评测维度聚合的统计指标。"""

    category: EvalCategoryName
    title: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    weighted_score: float
    duration_ms: float
    llm_calls: int = 0
    total_tokens: int = 0


class EvalReport(BaseModel):
    """整次评测运行输出报告（可直接序列化为 JSON 对比存档）。"""

    timestamp: str
    mode: EvalMode
    model: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    weighted_score: float
    duration_ms: float
    total_llm_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    categories: list[CategorySummary]
    cases: list[CaseReport]
