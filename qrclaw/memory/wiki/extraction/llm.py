"""
Wiki LLM Analyzer - LLM 调用封装

从 memory_extraction.py 迁移：
- WikiLLMAnalyzer.analyze (行 312-332)
- WikiLLMAnalyzer.consolidate (行 336-376)
"""

import json

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

    def __init__(self, llm_service, config: ExtractionConfig, schema_rules: str | None = None):
        """
        Args:
            llm_service: LLMService 实例
            config: ExtractionConfig 配置
        """
        self.llm = llm_service
        self.config = config
        from qrclaw.memory.wiki.schema import DEFAULT_WIKI_SCHEMA
        self.schema_rules = schema_rules or DEFAULT_WIKI_SCHEMA

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
        prompt = self.schema_rules + "\n\n" + prompt

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
        existing_metadata: dict | None = None,
        new_metadata: dict | None = None,
    ) -> ConsolidatePageSchema:
        """
        合并整理页面内容

        Args:
            existing_content: 现有页面内容
            new_content: 新内容片段
            page_name: 页面名称
            existing_metadata: 旧页面的摘要、标签与关联
            new_metadata: 本次新增内容建议的摘要、标签与关联

        Returns:
            ConsolidatePageSchema: 整理后的内容
        """
        prompt = CONSOLIDATION_PROMPT_TEMPLATE.format(
            existing_content=existing_content,
            new_content=new_content,
            page_name=page_name,
            existing_metadata=json.dumps(existing_metadata or {}, ensure_ascii=False),
            new_metadata=json.dumps(new_metadata or {}, ensure_ascii=False),
        )
        prompt = self.schema_rules + "\n\n" + prompt

        return self.llm.structured(
            messages=[{"role": "user", "content": prompt}],
            response_model=ConsolidatePageSchema,
            max_retries=self.config.max_retries,
        )
