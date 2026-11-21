"""记忆提取的写入执行器：调用提取模型，并把结果按来源和版本校验后写入 Wiki。"""

import time
from typing import Optional
from datetime import datetime

from qrclaw.logger import get_logger

from qrclaw.memory.wiki.extraction.config import ExtractionConfig, DEFAULT_CONFIG
from qrclaw.memory.wiki.extraction.llm import WikiLLMAnalyzer

logger = get_logger("qrclaw.memory.wiki.extraction.runner")


class ExtractionRunner:
    """自动提取、主动记忆和来源修复共用的模型调用与页面写入。"""

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
        # 最近一次处理结果实际写入的页面名，供主动记忆把页面告诉界面。
        self.last_written_pages: list[str] = []

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
            extraction_data: 包含 index_summary（当前索引）和 messages_text（待分析正文）

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

    # ── 批量写入 ──────────────────────────────────────────────────────────────

    def process_extraction_result(self, extraction: "ExtractionSchema", is_current=None, save_page=None,
                                  *, sources: list[dict] | None = None, batch_id: str | None = None,
                                  explicit: bool = False, started_at: float | None = None) -> int:
        """
        处理提取结果；自动任务在保存前确认来源消息尚未被编辑或删除。

        Args:
            extraction: ExtractionSchema 结果

        Returns:
            int: 成功写入的数量
        """
        self.last_written_pages = []
        if not extraction or not extraction.needs_update or not extraction.pages:
            return 0

        success_count = 0
        failures = []
        persist_page = save_page or self.wiki_memory.save_page
        # 主动记忆传入请求开始时间：只有晚于删除时间的明确请求才能重建被删除的页面。
        started_at = time.time() if started_at is None else started_at

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
                self.last_written_pages.append(page.name)
                logger.info(f"[ExtractionRunner] 写入成功: [{action}] {page.name}")

            except Exception as e:
                logger.warning(f"[ExtractionRunner] 写入页面失败: {page.name}, {e}")
                failures.append(page.name)

        if failures:
            raise RuntimeError(f"Wiki 页面写入失败: {', '.join(failures)}（已写入 {success_count} 个）")

        return success_count


# ── Schema 类型提示 ──────────────────────────────────────────────────────────
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema
