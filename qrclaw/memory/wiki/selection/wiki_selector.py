"""
Wiki Selector - 基于 LLM 的 Wiki Page 智能选择器

核心功能：
- 根据对话上下文，智能选择最相关的 Wiki 页面
- 继承主 agent 的 3 轮对话历史
- 过滤工具调用信息，只保留纯对话内容

使用方法：
    from qrclaw.memory.wiki.selection import WikiPageSelector
    
    selector = WikiPageSelector(wiki_memory)
    result = selector.select(current_message, messages)
    for page in result.selected:
        print(f"{page.name}: {page.reason} ({page.relevance})")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qrclaw.memory.wiki.wiki_memory import WikiMemory

from qrclaw.memory.wiki.selection.prompts import SELECTION_PROMPT_TEMPLATE
from qrclaw.memory.wiki.selection.schemas import (
    SelectedPage,
    SelectionResult,
)
from qrclaw.llm import chat
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.wiki.selector")

# 默认保留的对话轮数
DEFAULT_CONVERSATION_ROUNDS = 3

# 过滤工具调用的角色
TOOL_ROLES = {"tool", "assistant"}  # assistant 可能包含 tool_calls


def _format_conversation_history(
    messages: list[dict],
    keep_rounds: int = DEFAULT_CONVERSATION_ROUNDS,
) -> str:
    """
    格式化对话历史，过滤工具调用信息。

    规则：
    1. 保留最近 N 轮对话（默认 3 轮）
    2. 过滤掉包含 tool_calls 或 tool_role 的消息
    3. 过滤掉系统消息（避免 prompt 过长）
    4. 只保留 role=user 和 role=assistant 的纯对话

    Args:
        messages: 原始消息列表
        keep_rounds: 保留的对话轮数

    Returns:
        格式化后的对话文本
    """
    if not messages:
        return "（无对话历史）"

    # 过滤并收集有效对话
    filtered_messages = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # 跳过系统消息
        if role == "system":
            continue

        # 跳过工具调用消息
        if role == "tool":
            continue

        # 跳过纯工具调用消息（tool_calls 存在但 content 为空）
        if role == "assistant" and msg.get("tool_calls") and not content:
            continue

        # 保留有 content 的消息（包括 tool_calls 和 content 共存的 assistant 消息）
        if content and content.strip():
            filtered_messages.append(msg)

    # 只保留最近 N 轮
    recent = filtered_messages[-keep_rounds:] if len(filtered_messages) > keep_rounds else filtered_messages

    # 格式化
    lines = []
    for msg in recent:
        role = msg.get("role", "unknown")
        content = msg.get("content", "").strip()

        if role == "user":
            lines.append(f"**用户**: {content}")
        elif role == "assistant":
            lines.append(f"**助手**: {content}")
        else:
            lines.append(f"**{role}**: {content}")

        lines.append("")  # 空行分隔

    return "\n".join(lines) if lines else "（无有效对话历史）"


class WikiPageSelector:
    """
    基于 LLM 的 Wiki Page 智能选择器

    使用方法：
        selector = WikiPageSelector(wiki_memory)
        result = selector.select(
            current_message="如何配置 LLM provider?",
            messages=[...],  # 主 agent 的 messages
        )
    """

    def __init__(self, wiki_memory: "WikiMemory"):
        """
        Args:
            wiki_memory: WikiMemory 实例
        """
        self.wiki_memory = wiki_memory

    def select(
        self,
        current_message: str,
        messages: list[dict],
        keep_rounds: int = DEFAULT_CONVERSATION_ROUNDS,
    ) -> SelectionResult:
        """
        根据对话上下文选择最相关的 Wiki 页面。

        Args:
            current_message: 用户当前消息
            messages: 主 agent 的完整 messages 列表
            keep_rounds: 保留的对话轮数（默认 3）

        Returns:
            SelectionResult: 包含选中的页面列表和选择理由
        """
        # 1. 获取 Wiki 索引
        index_md = self.wiki_memory.load_index()

        # 2. 格式化对话历史（过滤工具调用）
        conversation_history = _format_conversation_history(messages, keep_rounds)

        # 3. 构建 prompt
        prompt = SELECTION_PROMPT_TEMPLATE.format(
            index_md=index_md,
            conversation_history=conversation_history,
            current_message=current_message,
        )

        # 4. 调用 LLM
        logger.debug(f"WikiPageSelector 调用 LLM，conversation_history 长度: {len(conversation_history)}")

        try:
            response = chat([
                {"role": "user", "content": prompt},
            ])
            # llm.chat 可能返回 LLMResponse 或 str，统一处理
            response_text = response.content if hasattr(response, 'content') else str(response)
        except Exception as e:
            logger.error(f"WikiPageSelector LLM 调用失败: {e}", exc_info=True)
            return SelectionResult(selected=[], reasoning=f"LLM 调用失败: {e}")

        # 5. 解析响应
        return self._parse_response(response_text)

    def _parse_response(self, response: str) -> SelectionResult:
        """
        解析 LLM 响应，提取选择结果。

        尝试 JSON 解析，失败时返回空结果。
        """
        import json
        import re

        # 清理响应文本，提取 JSON
        # 常见格式: ```json\n{...}\n``` 或直接是 {...}
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析
            json_str = response.strip()

        # 去掉可能的前后空白和 markdown 代码块标记
        json_str = json_str.strip().strip("```").strip()

        try:
            data = json.loads(json_str)
            selected_pages = [
                SelectedPage(
                    name=p.get("name", ""),
                    reason=p.get("reason", ""),
                    relevance=p.get("relevance", 0.0),
                )
                for p in data.get("selected", [])
                if p.get("name")  # 跳过无名字段
            ]
            reasoning = data.get("reasoning", "")
            result = SelectionResult(selected=selected_pages, reasoning=reasoning)
            logger.debug(f"WikiPageSelector 解析成功，选择 {len(selected_pages)} 个页面")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"WikiPageSelector JSON 解析失败: {e}, 响应: {response[:200]}...")
            return SelectionResult(selected=[], reasoning=f"JSON 解析失败")

    def select_with_pages(
        self,
        current_message: str,
        messages: list[dict],
        keep_rounds: int = DEFAULT_CONVERSATION_ROUNDS,
    ) -> list["WikiPage"]:
        """
        选择页面并返回 WikiPage 对象列表。

        与 select() 相同，但返回完整 WikiPage 对象而非 name。
        """
        from qrclaw.memory.wiki.page import WikiPage

        result = self.select(current_message, messages, keep_rounds)

        pages = []
        for selected in result.selected:
            page = self.wiki_memory.get_page(selected.name)
            if page:
                pages.append(page)
            else:
                logger.warning(f"WikiPageSelector: 页面不存在 {selected.name}")

        return pages
