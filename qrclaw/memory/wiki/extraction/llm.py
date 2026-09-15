"""复用 LLMService 的结构化调用和重试，分析或整理 Wiki 页面。"""
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema, ConsolidatePageSchema
from qrclaw.memory.wiki.extraction.prompts import EXTRACTION_PROMPT_TEMPLATE, CONSOLIDATION_PROMPT_TEMPLATE


class WikiLLMAnalyzer:
    """将记忆分析任务转换为经过 Schema 验证的结构化结果。"""

    def __init__(self, llm_service, config: ExtractionConfig):
        """使用现有模型服务和重试配置。"""
        self.llm = llm_service
        self.config = config

    def analyze(self, index_md: str, messages_text: str) -> ExtractionSchema:
        """结合已有索引提取需要保存的长期知识。"""
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(index_md=index_md, messages_text=messages_text)
        return self.llm.structured(
            [{"role": "user", "content": prompt}], ExtractionSchema, max_retries=self.config.max_retries,
        )

    def consolidate(self, existing_content: str, new_content: str, page_name: str) -> ConsolidatePageSchema:
        """合并页面内容并生成精炼的摘要和标签。"""
        prompt = CONSOLIDATION_PROMPT_TEMPLATE.format(
            existing_content=existing_content, new_content=new_content, page_name=page_name,
        )
        result = self.llm.structured(
            [{"role": "user", "content": prompt}], ConsolidatePageSchema, max_retries=self.config.max_retries,
        )
        if not result.content.strip():
            raise ValueError("Wiki 整理返回空正文，保留原页面")
        return result
