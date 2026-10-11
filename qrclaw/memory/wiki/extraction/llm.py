"""
Wiki LLM Analyzer - LLM 调用封装

从 memory_extraction.py 迁移：
- WikiLLMAnalyzer.analyze (行 312-332)
- WikiLLMAnalyzer.consolidate (行 336-376)
"""

from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.schemas import (
    ExtractionSchema,
    ConsolidatePageSchema,
)
from qrclaw.memory.wiki.extraction.prompts import (
    EXTRACTION_PROMPT_TEMPLATE,
    CONSOLIDATION_PROMPT_TEMPLATE,
)


class WikiLLMAnalyzer:
    """Wiki LLM 分析器封装"""

    def __init__(self, llm_service, config: ExtractionConfig):
        """
        Args:
            llm_service: LLMService 实例
            config: ExtractionConfig 配置
        """
        self.llm = llm_service
        self.config = config

    def analyze(
        self,
        index_md: str,
        messages_text: str,
    ) -> ExtractionSchema:
        """
        分析对话历史，提取 Wiki 页面

        Args:
            index_md: Wiki 索引内容
            messages_text: 格式化后的对话历史

        Returns:
            ExtractionSchema: 包含 needs_update 和 pages 列表
        """
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            index_md=index_md,
            messages_text=messages_text,
        )

        return self.llm.structured(
            messages=[{"role": "user", "content": prompt}],
            response_model=ExtractionSchema,
            max_retries=self.config.max_retries,
        )

    def consolidate(
        self,
        existing_content: str,
        new_content: str,
        page_name: str,
    ) -> ConsolidatePageSchema:
        """
        合并整理页面内容

        Args:
            existing_content: 现有页面内容
            new_content: 新内容片段
            page_name: 页面名称

        Returns:
            ConsolidatePageSchema: 整理后的内容
        """
        prompt = CONSOLIDATION_PROMPT_TEMPLATE.format(
            existing_content=existing_content,
            new_content=new_content,
            page_name=page_name,
        )

        return self.llm.structured(
            messages=[{"role": "user", "content": prompt}],
            response_model=ConsolidatePageSchema,
            max_retries=self.config.max_retries,
        )
