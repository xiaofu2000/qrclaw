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
from datetime import datetime

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
        self.llm_analyzer = WikiLLMAnalyzer(get_llm_service(), self.config,
            schema_rules=(self.wiki_memory.memory_dir / "SCHEMA.md").read_text(encoding="utf-8"))

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
                existing_metadata=dict(description=page.description, tags=page.tags, related=page.related),
            )
            self.wiki_memory.save_page(
                name=page.name,
                content=result.content,
                description=result.description or page.description,
                tags=result.tags if "tags" in result.model_fields_set else page.tags,
                related=result.related if "related" in result.model_fields_set else page.related,
                expected_page=page,
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
        """使用触发统计共用的过滤规则，仅发送用户和助手正文。"""
        from qrclaw.memory.wiki.extraction.strategies import format_messages_for_llm
        return format_messages_for_llm(messages)

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

    def process_extraction_result(self, extraction: "ExtractionSchema", is_current=None, save_page=None,
                                  *, sources: list[dict] | None = None, batch_id: str | None = None,
                                  explicit: bool = False) -> int:
        """
        处理提取结果；自动任务在保存前确认来源消息尚未被编辑或删除。

        Args:
            extraction: ExtractionSchema 结果

        Returns:
            int: 成功写入的数量
        """
        if not extraction or not extraction.needs_update or not extraction.pages:
            return 0

        success_count = 0
        failures = []
        persist_page = save_page or self.wiki_memory.save_page
        import time
        started_at = getattr(self, "started_at", time.time())

        grouped = {}
        for proposed in extraction.pages:
            if proposed.action == "skip":
                continue
            name = self.wiki_memory.fuzzy_find_name(proposed.name) or proposed.name
            if name not in grouped:
                grouped[name] = proposed.model_copy(deep=True)
                grouped[name].name = name
            else:
                current = grouped[name]
                if proposed.content and proposed.content not in current.content:
                    current.content += "\n\n" + proposed.content
                current.source_ids = list(dict.fromkeys(current.source_ids + proposed.source_ids))
                current.tags = list(dict.fromkeys(current.tags + proposed.tags))
                current.related = list(dict.fromkeys(current.related + proposed.related))

        for page in grouped.values():
            try:
                if is_current is not None and not is_current():
                    return success_count
                if page.action == "skip" or not page.content.strip():
                    continue

                page.name = self.wiki_memory.fuzzy_find_name(page.name) or page.name
                with self.wiki_memory._lock:
                    deleted_at = self.wiki_memory.provenance.read()["deleted_pages"].get(page.name)
                if deleted_at and (not explicit or datetime.fromisoformat(deleted_at).timestamp() >= started_at):
                    continue
                existing = self.wiki_memory.get_page(page.name)
                receipt = None
                source_ids = None
                new_content = page.content
                new_metadata = dict(description=page.description, tags=page.tags, related=page.related)
                if sources is not None:
                    allowed = {source["id"]: source for source in sources}
                    if not page.source_ids or any(key not in allowed for key in page.source_ids):
                        raise ValueError("模型必须引用输入提供的真实来源 ID")
                    if not any(allowed[key]["role"] == "user" for key in page.source_ids):
                        raise ValueError("不能仅根据助手推测创建记忆")
                    if batch_id:
                        import hashlib
                        receipt = batch_id + ":" + hashlib.sha256(page.name.encode()).hexdigest()
                    if existing and receipt in existing.applied_batches:
                        continue
                    if existing:
                        self.wiki_memory.provenance.baseline(existing)
                        with self.wiki_memory._lock:
                            state = self.wiki_memory.provenance.read()
                            manual = [state["sources"].get(key, {}) for key in existing.source_ids]
                        manual_order = max((s.get("order", 0) for s in manual if s.get("kind") == "manual"), default=0)
                        if manual_order:
                            page.source_ids = [key for key in page.source_ids if allowed[key].get("order", 0) > manual_order]
                            if not page.source_ids or not any(allowed[key]["role"] == "user" for key in page.source_ids):
                                continue
                            # 混合批次的模型摘要可能夹带旧事实，只提交用户确认之后的新证据。
                            from qrclaw.memory.wiki.provenance import source_text
                            new_content = "尊重现有用户确认正文；仅以下较新的证据可补充或明确纠正，不能补回旧来源被移除的事实。\n" + source_text([allowed[key] for key in page.source_ids])
                            new_metadata = None
                    source_ids = list(dict.fromkeys((existing.source_ids if existing else []) + page.source_ids))
                if existing:
                    if self.llm_analyzer is None:
                        self._init_llm_analyzer()
                    result = self.llm_analyzer.consolidate(
                        existing_content=existing.content,
                        new_content=new_content,
                        page_name=page.name,
                        existing_metadata=dict(description=existing.description, tags=existing.tags, related=existing.related),
                        new_metadata=new_metadata,
                    )
                    content = result.content.strip()
                    description = result.description or existing.description
                    tags = result.tags if "tags" in result.model_fields_set else existing.tags
                    related = result.related if "related" in result.model_fields_set else existing.related
                    if is_current is not None and not is_current():
                        return success_count
                    if (content, description, tags, related) == (
                        existing.content, existing.description, existing.tags, existing.related
                    ) and (source_ids is None or source_ids == existing.source_ids):
                        continue
                    saved = persist_page(
                        name=page.name,
                        content=content,
                        description=description,
                        tags=tags,
                        related=related,
                        expected_page=existing,
                        source_ids=source_ids, batch_id=receipt, explicit=explicit, started_at=started_at,
                    )
                    action = "更新"
                else:
                    if self.wiki_memory.page_exists(page.name):
                        raise RuntimeError(f"无法读取页面「{page.name}」的旧内容，保留原文件")
                    saved = persist_page(
                        name=page.name,
                        content=page.content,
                        description=page.description,
                        tags=page.tags,
                        related=page.related,
                        source_ids=source_ids, batch_id=receipt, explicit=explicit, expected_missing=True, started_at=started_at,
                    )
                    action = "新建"

                if not saved:
                    continue
                success_count += 1
                logger.info(f"[ExtractionRunner] 写入成功: [{action}] {page.name}")

            except Exception as e:
                logger.warning(f"[ExtractionRunner] 写入页面失败: {page.name}, {e}")
                failures.append(page.name)

        if failures:
            raise RuntimeError(f"Wiki 页面写入失败: {', '.join(failures)}（已写入 {success_count} 个）")

        return success_count


# ── Schema 类型提示 ──────────────────────────────────────────────────────────
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema
