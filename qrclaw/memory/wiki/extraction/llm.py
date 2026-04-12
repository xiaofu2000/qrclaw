"""
Wiki LLM Analyzer - LLM 调用封装

从 memory_extraction.py 迁移：
- WikiLLMAnalyzer.analyze (行 312-332)
- WikiLLMAnalyzer.consolidate (行 336-376)
"""

try:
    import instructor
    _HAS_INSTRUCTOR = True
except ImportError:
    _HAS_INSTRUCTOR = False

from pydantic import ValidationError

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

    def __init__(self, provider, config: ExtractionConfig):
        """
        Args:
            provider: LiteLLMProvider 实例
            config: ExtractionConfig 配置
        """
        self.provider = provider
        self.config = config
        self._client = None

    def _get_client(self):
        """获取 instructor 封装的 client"""
        if not _HAS_INSTRUCTOR:
            raise ImportError(
                "The 'instructor' package is required for WikiLLMAnalyzer. "
                "Install it with: pip install instructor"
            )
        if self._client is None:
            # 适配 LiteLLMProvider，它使用 chat 方法而不是 create
            self._client = instructor.patch(
                create=self._chat_wrapper,
                mode=instructor.Mode.JSON,
            )
        return self._client

    def _chat_wrapper(self, messages, **kwargs):
        """包装 provider.chat 为 instructor 需要的 create 接口"""
        response = self.provider.chat(messages=messages, json_mode=True)
        # 转换为 instructor 期望的格式
        class MockResponse:
            def __init__(self, content):
                self.choices = [type('Choice', (), {
                    'message': type('Message', (), {
                        'content': content
                    })()
                })()]
        return MockResponse(response.content)

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

        client = self._get_client()

        for attempt in range(self.config.max_retries):
            try:
                response = client(
                    messages=[
                        {"role": "user", "content": prompt},
                    ],
                    response_model=ExtractionSchema,
                    **self.provider.default_params,
                )
                return response
            except ValidationError as e:
                if attempt == self.config.max_retries - 1:
                    raise
                continue

        return ExtractionSchema(needs_update=False, pages=[])

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

        client = self._get_client()

        for attempt in range(self.config.max_retries):
            try:
                response = client(
                    messages=[
                        {"role": "user", "content": prompt},
                    ],
                    response_model=ConsolidatePageSchema,
                    **self.provider.default_params,
                )
                return response
            except ValidationError as e:
                if attempt == self.config.max_retries - 1:
                    raise
                continue

        return ConsolidatePageSchema(
            content=new_content,
            description="",
            tags=[],
        )
