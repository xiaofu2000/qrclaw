"""
ExtractionRunner - 记忆提取逻辑执行器

从 memory_extraction.py 拆分：
- Node 保留编排职责（状态追踪、队列管理、线程控制）
- ExtractionRunner 封装纯逻辑方法（LLM 调用、消息格式化、提取策略）

使用方式：
```python
runner = ExtractionRunner(wiki_memory, config)
runner.trigger_extraction(messages)        # 触发提取
result = runner.analyze_with_llm(prompt)   # LLM 分析
runner.consolidate_page(name)              # 整理页面
```
"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from qrclaw.logger import get_logger

from qrclaw.memory.wiki.extraction.config import ExtractionConfig, DEFAULT_CONFIG
from qrclaw.memory.wiki.extraction.llm import WikiLLMAnalyzer
from qrclaw.memory.wiki.extraction.prompts import EXTRACTION_PROMPT_TEMPLATE

logger = get_logger("qrclaw.memory.wiki.extraction.runner")


class ExtractionRunner:
    """
    记忆提取逻辑执行器

    封装以下纯逻辑方法：
    - trigger_extraction: 构建提取 prompt
    - analyze_with_llm: 调用 LLM 分析
    - consolidate_page: 整理页面内容
    - format_messages_for_llm: 消息格式化
    - messages_since_last_extraction: 截取新增消息
    """

    def __init__(
        self,
        wiki_memory,
        config: ExtractionConfig = None,
        llm_analyzer: WikiLLMAnalyzer = None,
    ):
        """
        Args:
            wiki_memory: WikiMemory 实例
            config: 提取配置
            llm_analyzer: WikiLLMAnalyzer 实例（懒加载）
        """
        self.wiki_memory = wiki_memory
        self.config = config or DEFAULT_CONFIG
        self.llm_analyzer = llm_analyzer

    # ── 提取触发 ──────────────────────────────────────────────────────────────

    def trigger_extraction(self, messages: list, last_message_uuid: str = None) -> dict:
        """
        触发提取：构建提取所需的结构化数据

        Args:
            messages: 对话历史
            last_message_uuid: 上次提取的最后一条消息 UUID

        Returns:
            dict: 包含 index_summary 和 messages_text 的结构化数据
        """
        # 只取上次提取之后新增的消息
        new_messages = self.messages_since_last_extraction(messages, last_message_uuid)
        messages_text = self.format_messages_for_llm(new_messages)
        index_summary = self.build_index_summary()

        logger.debug(f"[ExtractionRunner] 触发提取，消息数: {len(new_messages)}")
        return {
            "index_summary": index_summary,
            "messages_text": messages_text,
        }

    def build_prompt(self, extraction_data: dict) -> str:
        """
        根据结构化数据构建 LLM prompt

        Args:
            extraction_data: trigger_extraction 返回的结构化数据

        Returns:
            str: 格式化后的 prompt
        """
        return EXTRACTION_PROMPT_TEMPLATE.format(
            index_md=extraction_data["index_summary"],
            messages_text=extraction_data["messages_text"],
        )

    def build_index_summary(self) -> str:
        """构建索引摘要"""
        entries = self.wiki_memory.index.all_entries()
        if entries:
            return "\n".join(
                f"- {e['name']}：{e.get('description', '')}" for e in entries
            )
        return "（暂无页面）"

    # ── LLM 分析 ──────────────────────────────────────────────────────────────

    def analyze_with_llm(self, extraction_data: dict) -> Optional["ExtractionSchema"]:
        """
        使用 WikiLLMAnalyzer 进行 LLM 分析

        Args:
            extraction_data: trigger_extraction 返回的结构化数据，包含 index_summary 和 messages_text

        Returns:
            ExtractionSchema 或 None（失败时）
        """
        try:
            if self.llm_analyzer is None:
                self._init_llm_analyzer()

            logger.warning("[ExtractionRunner] 开始调用 LLM 分析...")

            result = self.llm_analyzer.analyze(
                extraction_data["index_summary"],
                extraction_data["messages_text"],
            )
            logger.warning(
                f"[ExtractionRunner] LLM分析完成: needs_update={result.needs_update}, "
                f"pages={len(result.pages)}"
            )
            return result

        except Exception as e:
            logger.warning(f"[ExtractionRunner] LLM分析失败: {e}", exc_info=True)
            return None

    def _init_llm_analyzer(self):
        """懒加载初始化 LLM analyzer"""
        from qrclaw.llm_service import get_llm_service
        from qrclaw.memory.wiki.extraction import WikiLLMAnalyzer
        self.llm_analyzer = WikiLLMAnalyzer(get_llm_service(), self.config)

    # ── 页面整理 ──────────────────────────────────────────────────────────────

    def consolidate_page(self, name: str) -> bool:
        """
        整理页面内容：去重、合并、保持精炼

        Args:
            name: 页面名称

        Returns:
            bool: 是否成功
        """
        page = self.wiki_memory.get_page(name)
        if not page:
            return False

        try:
            if self.llm_analyzer is None:
                self._init_llm_analyzer()

            result = self.llm_analyzer.consolidate(
                existing_content=page.content,
                new_content="",
                page_name=page.name,
            )
            self.wiki_memory.save_page(
                name=page.name,
                content=result.content,
                description=result.description or page.description,
                tags=result.tags if result.tags else page.tags,
                related=page.related,
            )
            logger.info(f"[ExtractionRunner] 页面整理完成: {name}")
            return True

        except Exception as e:
            logger.warning(f"[ExtractionRunner] 页面整理失败: {name}, {e}")
            return False

    def consolidate_pages(self, names: list[str], max_workers: int = 4) -> dict:
        """
        批量整理页面

        Args:
            names: 页面名称列表
            max_workers: 最大并发数

        Returns:
            dict: {name: success}
        """
        unique_names = list(dict.fromkeys(names))  # 去重保序
        logger.info(f"[ExtractionRunner] 批量整理 {len(unique_names)} 个页面")

        results = {}
        with ThreadPoolExecutor(max_workers=min(len(unique_names), max_workers)) as executor:
            futures = {
                executor.submit(self.consolidate_page, name): name
                for name in unique_names
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    logger.warning(f"[ExtractionRunner] 整理异常: {name}, {e}")
                    results[name] = False

        return results

    # ── 消息处理 ──────────────────────────────────────────────────────────────

    def format_messages_for_llm(self, messages: list) -> str:
        """
        将消息列表格式化为 LLM 可读的文本

        过滤规则：
        - 跳过 role=tool 的消息
        - 跳过含 tool_calls 的 assistant 消息
        - 保留 role=user 和纯文字 role=assistant 消息

        Args:
            messages: 消息列表

        Returns:
            str: 格式化后的文本
        """
        lines = []
        for msg in messages:
            role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")

            # 跳过 tool 返回
            if role == "tool":
                continue

            # 跳过含 tool_calls 的 assistant 消息
            if role == "assistant":
                tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(
                    msg, "tool_calls", None
                )
                if tool_calls:
                    continue

            content = msg.get("content", "") if isinstance(msg, dict) else getattr(
                msg, "content", ""
            )
            if not content:
                continue

            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "") or b.get("content", "")
                    for b in content
                    if b.get("type") == "text"
                )

            if content.strip():
                lines.append(f"[{role}] {content}")

        return "\n\n".join(lines)

    def messages_since_last_extraction(
        self, messages: list, last_message_uuid: str = None
    ) -> list:
        """
        返回上次提取之后新增的消息

        Args:
            messages: 全部消息列表
            last_message_uuid: 上次提取的最后一条消息 UUID

        Returns:
            list: 新增的消息列表
        """
        if last_message_uuid is None:
            return messages

        found = False
        result = []
        for msg in messages:
            if not found:
                msg_uuid = msg.get("uuid") if isinstance(msg, dict) else getattr(
                    msg, "uuid", None
                )
                if msg_uuid == last_message_uuid:
                    found = True
                continue
            result.append(msg)

        if not found:
            logger.warning(
                f"[ExtractionRunner] 未找到截断点 uuid={last_message_uuid}，回退全量消息"
            )
            return messages

        return result

    # ── 批量写入 ──────────────────────────────────────────────────────────────

    def process_extraction_result(self, extraction: "ExtractionSchema") -> int:
        """
        处理 LLM 提取结果，执行写入

        Args:
            extraction: ExtractionSchema 结果

        Returns:
            int: 成功写入的数量
        """
        if not extraction or not extraction.needs_update or not extraction.pages:
            return 0

        success_count = 0
        pages_to_consolidate = []

        for page in extraction.pages:
            try:
                # A. 模糊匹配：修正 LLM 填的名字
                if page.action == "append":
                    real_name = self.wiki_memory.fuzzy_find_name(page.name)
                    if real_name:
                        page.name = real_name
                    else:
                        logger.warning(
                            f"[ExtractionRunner] append页面不存在，降级为create: {page.name}"
                        )
                        page.action = "create"

                if page.action == "append":
                    self.wiki_memory.append_page(
                        name=page.name,
                        content=page.content,
                        tags=page.tags or None,
                        related=page.related or None,
                    )
                    pages_to_consolidate.append(page.name)
                    success_count += 1
                elif page.action == "create":
                    self.wiki_memory.save_page(
                        name=page.name,
                        content=page.content,
                        description=page.description,
                        tags=page.tags,
                        related=page.related,
                    )
                    success_count += 1

                logger.info(f"[ExtractionRunner] 写入成功: [{page.action}] {page.name}")

            except Exception as e:
                logger.warning(f"[ExtractionRunner] 写入页面失败: {page.name}, {e}")

        # 整理 append 的页面
        if pages_to_consolidate:
            self.consolidate_pages(pages_to_consolidate)

        return success_count


# ── Schema 类型提示 ──────────────────────────────────────────────────────────
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema
