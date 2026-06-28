"""
Wiki Page Selector - 基于 LLM 的智能页面选择器

继承主 agent 3 轮对话上下文，过滤工具调用信息，智能选择最相关的 Wiki page。
"""

import logging
from dataclasses import dataclass

import instructor
from pydantic import BaseModel, Field

from qrclaw.memory.wiki.page import WikiPage

logger = logging.getLogger(__name__)


# =============================================================================
# Schema 定义
# =============================================================================


class PageSelection(BaseModel):
    """单页面选择结果"""

    name: str = Field(..., description="Wiki page 名称")
    relevance_score: float = Field(..., ge=0.0, le=1.0, description="相关性评分 0-1")
    reason: str = Field(..., description="选择该页面的原因")


class SelectionSchema(BaseModel):
    """LLM 返回的结构化选择结果"""

    selected_pages: list[PageSelection] = Field(
        default_factory=list, description="选中的页面列表"
    )
    reasoning: str = Field(..., description="整体选择逻辑和推理过程")


@dataclass
class SelectionResult:
    """选择结果数据类"""

    selected_pages: list[WikiPage]
    reasoning: str

    @classmethod
    def from_schema(
        cls, schema: SelectionSchema, pages_map: dict[str, WikiPage]
    ) -> "SelectionResult":
        """从 LLM 返回的 schema 转换为 SelectionResult"""
        selected = []
        for page_sel in schema.selected_pages:
            if page_sel.name in pages_map:
                selected.append(pages_map[page_sel.name])
        return cls(selected_pages=selected, reasoning=schema.reasoning)


# =============================================================================
# System Prompt
# =============================================================================

SELECTOR_SYSTEM_PROMPT = """\
你是一位专业的知识库助手，负责从大量 Wiki 页面中筛选出与当前对话最相关的页面。

## 你的职责
根据用户问题和对话上下文，从给定的 Wiki 页面列表中选择最相关的子集。

## 选择标准
1. **主题相关性**：页面主题是否与用户问题直接相关
2. **信息价值**：页面内容是否能帮助回答用户问题
3. **时效性**：优先选择最近更新且内容完整的页面
4. **精确匹配**：页面名称、描述、标签与问题关键词的匹配程度

## 选择策略
- 通常选择 1-3 个最相关的页面，避免返回过多无关页面
- 如果没有页面相关，返回空列表并说明原因
- 对于模糊查询，选择最能解释概念的页面
- 对于具体问题，选择包含详细操作步骤的页面

## 输出要求
返回 JSON 格式，包含：
- `selected_pages`：选中的页面列表，每个包含 name/relevance_score/reason
- `reasoning`：整体选择逻辑和推理过程

**重要**：只输出 JSON，不要包含任何其他文字。\
"""

USER_PROMPT_TEMPLATE = """\
## 用户问题
{messages_text}

## 可选 Wiki 页面
{pages_md}

请选择最相关的页面。\
"""


# =============================================================================
# WikiPageSelector
# =============================================================================


class WikiPageSelector:
    """
    基于 LLM 的 Wiki Page 智能选择器

    使用方法：
        selector = WikiPageSelector()
        result = selector.select_relevant_pages(messages, pages)
        for page in result.selected_pages:
            print(f"- {page.name}: {result.reasoning}")
    """

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.0,
        max_history_rounds: int = 3,
    ):
        """
        初始化选择器

        Args:
            model: LLM 模型名称，默认使用配置中的模型
            temperature: 采样温度，0.0 表示确定性输出
            max_history_rounds: 保留的最大对话轮数
        """
        self.model = model
        self.temperature = temperature
        self.max_history_rounds = max_history_rounds

    def select_relevant_pages(
        self, messages: list[dict], pages: list[WikiPage]
    ) -> SelectionResult:
        """
        根据对话上下文选择最相关的 Wiki pages

        Args:
            messages: 对话消息列表，格式为 [{"role": "user"|"assistant"|..., "content": "..."}]
            pages: WikiPage 对象列表

        Returns:
            SelectionResult: 包含选中的页面列表和推理过程
        """
        # 过滤消息，保留最近 N 轮 user 消息
        filtered_messages = self._filter_messages(messages)

        if not filtered_messages:
            logger.warning("过滤后无有效消息，返回空选择结果")
            return SelectionResult(selected_pages=[], reasoning="无有效用户消息")

        if not pages:
            logger.warning("无可选页面，返回空选择结果")
            return SelectionResult(
                selected_pages=[], reasoning="没有可用的 Wiki 页面"
            )

        # 构建页面 Markdown 列表
        pages_md = self._format_pages(pages)
        pages_map = {p.name: p for p in pages}

        # 构建用户提示
        messages_text = self._format_messages(filtered_messages)
        user_prompt = USER_PROMPT_TEMPLATE.format(
            messages_text=messages_text, pages_md=pages_md
        )

        # 调用 LLM（使用 instructor 结构化输出）
        try:
            from qrclaw.llm_service import get_llm_service

            llm = get_llm_service()
            client = instructor.patch(
                create=llm.create_openai_like,
                # 使用 MD_JSON 模式，避免依赖 response_format=json_object（部分模型不支持）
                mode=instructor.Mode.MD_JSON,
            )

            schema = client(
                messages=[
                    {"role": "system", "content": SELECTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_model=SelectionSchema,
                temperature=self.temperature,
                max_retries=2,
            )
            return SelectionResult.from_schema(schema, pages_map)

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return SelectionResult(
                selected_pages=[], reasoning=f"LLM 调用失败: {str(e)}"
            )

    def _filter_messages(self, messages: list[dict]) -> list[dict]:
        """
        过滤消息：只保留 user 角色，过滤工具调用和 assistant 消息

        Args:
            messages: 原始消息列表

        Returns:
            过滤后的消息列表，只包含 user 角色的消息
        """
        filtered = []

        # 只保留 user 角色的消息
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            # 跳过非 user 消息
            if role != "user":
                continue

            # 跳过空消息或工具调用消息
            if not content or content.strip() == "":
                continue

            filtered.append(msg)

        # 只保留最近 max_history_rounds 轮
        return filtered[-self.max_history_rounds :]

    def _format_messages(self, messages: list[dict]) -> str:
        """
        将消息列表格式化为可读文本

        Args:
            messages: 消息列表

        Returns:
            格式化后的文本
        """
        if not messages:
            return ""

        lines = []
        for i, msg in enumerate(messages, 1):
            content = msg.get("content", "")
            lines.append(f"[用户消息 {i}]\n{content}")

        return "\n\n".join(lines)

    def _format_pages(self, pages: list[WikiPage]) -> str:
        """
        将页面列表格式化为 Markdown 列表

        Args:
            pages: WikiPage 列表

        Returns:
            格式化的 Markdown 文本
        """
        if not pages:
            return "（无可用页面）"

        lines = []
        for page in pages:
            # 页面标题
            title = f"### {page.name}"
            lines.append(title)

            # 描述
            if page.description:
                lines.append(f"**描述**: {page.description}")

            # 标签
            if page.tags:
                lines.append(f"**标签**: {', '.join(page.tags)}")

            # 更新时间
            if page.updated_at:
                lines.append(f"**更新**: {page.updated_at}")

            # 内容预览（取前200字符）
            if page.content:
                preview = page.content[:200].replace("\n", " ")
                if len(page.content) > 200:
                    preview += "..."
                lines.append(f"**内容**: {preview}")

            lines.append("")  # 空行分隔

        return "\n".join(lines)


# =============================================================================
# 便捷函数
# =============================================================================


def select_wiki_pages(
    messages: list[dict], pages: list[WikiPage], **kwargs
) -> SelectionResult:
    """
    便捷函数：根据对话上下文选择 Wiki pages

    Args:
        messages: 对话消息列表
        pages: WikiPage 对象列表
        **kwargs: 传递给 WikiPageSelector 的参数

    Returns:
        SelectionResult: 选择结果
    """
    selector = WikiPageSelector(**kwargs)
    return selector.select_relevant_pages(messages, pages)
