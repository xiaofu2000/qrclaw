"""
WikiQueryNode —— 计划目标触发式 Wiki 知识精排注入节点

触发时机：在 RouterNode 判断 route="plan" 后，由 PlanExecutorNode 或 RouterNode
         调用本节点，对 plan_goal 进行 LLM 精排，从 WikiMemory 中召回最相关的页面正文，
         注入到后续执行节点的 System Prompt 上下文中。

核心流程：
  1. 接收 user_input + plan_goal 作为查询参数
  2. 调用 wiki.select_relevant_pages() 两阶段精排（关键词初筛 + LLM 精选）
  3. 调用 wiki.get_page() 获取选中页面的完整正文
  4. 返回 [{name, content, description, tags}] 列表，供注入使用

注入方式（由调用方决定）：
  - 拼接到 session messages 的 system prompt 中
  - 或作为 extra_context 传入子 agent 的 prompt
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from qrclaw.logger import get_logger
from qrclaw.memory.wiki.wiki_memory import WikiMemory

logger = get_logger("qrclaw.graph.nodes.wiki_query")


@dataclass
class WikiQueryResult:
    """WikiQueryNode 的返回值结构"""

    # 按相关性排序的页面列表（已注入正文）
    pages: list[dict]
    # 注入后的上下文字符串，可直接拼接到 prompt 中
    injected_context: str

    @property
    def is_empty(self) -> bool:
        return len(self.pages) == 0

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def __str__(self) -> str:
        return self.injected_context


def _build_injected_context(pages: list[dict]) -> str:
    """将页面列表构建为可直接注入的上下文字符串"""
    if not pages:
        return ""

    sections = ["## Wiki 知识库（相关页面）\n"]
    for i, page in enumerate(pages, 1):
        sections.append(f"### {i}. {page['name']}")
        if page.get("description"):
            sections.append(f"**摘要**: {page['description']}")
        if page.get("tags"):
            sections.append(f"**标签**: {', '.join(page['tags'])}")
        sections.append("")
        sections.append(page["content"])
        sections.append("\n---\n")

    return "\n".join(sections)


class WikiQueryNode:
    """
    计划目标触发式 Wiki 知识精排注入节点

    用法：
        wiki_query = WikiQueryNode(memory_dir=Path("/path/to/memory"))
        result = wiki_query.run(
            user_input="用户原始输入",
            plan_goal="任务目标描述",
            messages=session.messages,  # 继承对话上下文
        )
        # result.injected_context 可注入到后续 prompt
    """

    def __init__(
        self,
        memory_dir: Optional[str | Path] = None,
        top_k: int = 3,
    ):
        """
        Args:
            memory_dir: WikiMemory 工作目录，默认使用 ~/.qrclaw/agents/default/memory
            top_k: LLM 精排最多返回的页面数，默认 3
        """
        if memory_dir:
            self.memory_dir = Path(memory_dir)
        else:
            self.memory_dir = None  # 延迟到 run() 时从上下文获取

        self.top_k = top_k
        self._wiki: Optional[WikiMemory] = None

    @property
    def wiki(self) -> WikiMemory:
        """延迟初始化 WikiMemory 实例"""
        if self._wiki is None:
            if self.memory_dir:
                self._wiki = WikiMemory.for_workspace(self.memory_dir)
            else:
                # 尝试从全局上下文获取 memory_dir
                from qrclaw.memory.context.context_manager import get_context_manager

                ctx = get_context_manager()
                self._wiki = WikiMemory.for_workspace(ctx.workspace.memory_dir)
        return self._wiki

    def run(
        self,
        user_input: str,
        plan_goal: str,
        messages: Optional[list[dict]] = None,
    ) -> WikiQueryResult:
        """
        执行 Wiki 精排查询

        Args:
            user_input: 用户原始输入（参与关键词初筛）
            plan_goal: plan.goal 任务目标描述（主要查询参数）
            messages: 主 agent 的 messages 列表（继承对话上下文，用于 LLM 精选）

        Returns:
            WikiQueryResult: 包含 pages 列表和 injected_context 字符串
        """
        # 合并查询：优先使用 plan_goal，user_input 作为补充上下文
        query = plan_goal if plan_goal else user_input

        if not query:
            logger.debug("WikiQueryNode: query 为空，跳过查询")
            return WikiQueryResult(pages=[], injected_context="")

        logger.info(f"WikiQueryNode: 开始查询 query={query!r}")

        # 第一步：LLM 精排选择相关页面（两阶段）
        selected = self.wiki.select_relevant_pages(
            query=query,
            messages=messages or [],
            top_k=self.top_k,
        )

        if not selected:
            logger.info("WikiQueryNode: 精排无结果，返回空上下文")
            return WikiQueryResult(pages=[], injected_context="")

        logger.info(f"WikiQueryNode: LLM 选中 {len(selected)} 个页面: {[p.name for p in selected]}")

        # 第二步：构建页面详情列表（已包含正文）
        pages_detail: list[dict] = []
        for page in selected:
            pages_detail.append({
                "name": page.name,
                "content": page.content,
                "description": page.description,
                "tags": page.tags,
                "related": page.related,
            })

        # 第三步：构建可注入的上下文字符串
        injected_context = _build_injected_context(pages_detail)

        logger.info(f"WikiQueryNode: 构建上下文完成，共 {len(pages_detail)} 个页面")
        return WikiQueryResult(
            pages=pages_detail,
            injected_context=injected_context,
        )
