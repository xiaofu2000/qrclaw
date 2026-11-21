"""
Wiki Extraction Module - 从 memory_extraction.py 拆分出的提取逻辑

职责：
- 提示词模板 (prompts.py)
- Schema 定义 (schemas.py)
- 消息过滤与格式化 (strategies.py)
- LLM 调用 (llm.py)
- 配置常量 (config.py)
- 提取进度 (indexer.py)
- 写入执行 (runner.py)
"""

from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.schemas import (
    WikiPageSchema,
    ConsolidatePageSchema,
    ExtractionSchema,
)
# WikiLLMAnalyzer 需要 instructor 依赖，延迟导入
try:
    from qrclaw.memory.wiki.extraction.llm import WikiLLMAnalyzer
except ImportError:
    WikiLLMAnalyzer = None  # type: ignore
from qrclaw.memory.wiki.extraction.indexer import ExtractionIndexer
from qrclaw.memory.wiki.extraction.runner import ExtractionRunner

__all__ = [
    "ExtractionConfig",
    "WikiPageSchema",
    "ConsolidatePageSchema",
    "ExtractionSchema",
    "WikiLLMAnalyzer",
    "ExtractionIndexer",
    "ExtractionRunner",
]
