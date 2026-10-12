"""
WikiMemory —— LLM Wiki 记忆管理器

替代原有的 LongTermMemory，采用 LLM Wiki 架构：
- 所有页面平铺在 pages/ 目录
- index.json 作为结构化索引（代码用）
- index.md 作为人类可读目录（注入 system prompt）
- log.md 记录操作历史

核心操作：
- save_page：新建或更新页面（upsert 语义）
- get_page：读取指定页面
- list_pages：列出所有页面
- search_pages：关键词全文搜索
- delete_page：删除页面
- load_index：返回 index.md，供 system prompt 注入
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional
from filelock import FileLock

from qrclaw.logger import get_logger
from qrclaw.memory.wiki.index import IndexManager, atomic_write
from qrclaw.memory.wiki.page import WikiPage, wiki_links

logger = get_logger("qrclaw.memory.wiki.wiki_memory")

AGENTS_ROOT = Path.home() / ".qrclaw" / "agents"

# 按 memory_dir 缓存实例，同一目录永远返回同一个实例
_instances: dict[Path, "WikiMemory"] = {}


class WikiMemory:
    """
    LLM Wiki 记忆管理器

    用法：
        wiki = WikiMemory.for_workspace(memory_dir)  # 推荐，按目录缓存单例
        wiki = WikiMemory(memory_dir)                # 直接创建
        wiki.save_page("qrclaw架构", content="...", tags=["架构"])
        page = wiki.get_page("qrclaw架构")
        index = wiki.load_index()  # 注入 system prompt
    """

    @classmethod
    def for_workspace(cls, memory_dir: Path) -> "WikiMemory":
        """按 memory_dir 返回缓存实例，同一目录永远返回同一个对象"""
        key = Path(memory_dir).resolve()
        if key not in _instances:
            _instances[key] = cls(memory_dir)
        return _instances[key]

    def __init__(self, memory_dir: Path = None):
        if memory_dir:
            self.memory_dir = Path(memory_dir)
        else:
            self.memory_dir = AGENTS_ROOT / "default" / "memory"

        self.pages_dir = self.memory_dir / "pages"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.memory_dir / ".wiki.lock"))
        with self._lock:
            self.index = IndexManager(self.memory_dir)
            self.pages_dir.mkdir(parents=True, exist_ok=True)
            from qrclaw.memory.wiki.schema import DEFAULT_WIKI_SCHEMA
            schema = self.memory_dir / "SCHEMA.md"
            if not schema.exists():
                atomic_write(schema, DEFAULT_WIKI_SCHEMA)
        logger.debug(f"WikiMemory 初始化: {self.memory_dir}")
        from qrclaw.memory.wiki.provenance import WikiProvenance
        self.provenance = WikiProvenance(self)
        self._repair_worker = None

    # ── 核心 CRUD ─────────────────────────────────────────────────────────────

    def _page_path(self, name: str) -> Path:
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name.encode("utf-8")) > 252 or any(c in name for c in "/\\\r\n\0"):
            raise ValueError(f"无效的 Wiki 页面名称: {name!r}")
        path = self.pages_dir / f"{name}.md"
        if not path.resolve().is_relative_to(self.pages_dir.resolve()):
            raise ValueError(f"无效的 Wiki 页面名称: {name!r}")
        return path

    def save_page(
        self,
        name: str,
        content: str,
        description: str = "",
        tags: list[str] = None,
        related: list[str] = None,
        *,
        expected_page: WikiPage | None = None,
        expected_missing: bool = False,
        source_ids: list[str] | None = None,
        status: str | None = None,
        batch_id: str | None = None,
        allow_invalid_sources: bool = False,
        explicit: bool = False,
        started_at: float | None = None,
    ) -> WikiPage:
        """保存页面；模型整理结果须基于未被其他任务修改的旧页面。"""
        with self._lock:
            state = self.provenance.read()
            if name in state["deleted_pages"]:
                deleted_at = datetime.fromisoformat(state["deleted_pages"][name]).timestamp()
                if not explicit or started_at is None or deleted_at >= started_at:
                    raise RuntimeError(f"页面「{name}」已被主动删除，拒绝旧任务恢复")
            current = self.get_page(name)
            if batch_id and current and batch_id in current.applied_batches:
                return current
            if expected_missing and self.page_exists(name):
                raise RuntimeError(f"页面「{name}」已由其他任务创建，请重新读取")
            if source_ids is not None and not allow_invalid_sources and not self.provenance.current(source_ids):
                raise RuntimeError("记忆来源已被撤销，拒绝写入旧结果")
            if expected_page is not None:
                if not content.strip() and status != "inactive":
                    raise ValueError("整理后的页面正文为空，保留原页面")
                current = self.get_page(name)
                # 旧格式页面可能没有时间戳，按正文和元数据检查并发变化。
                if current is None or any(
                    getattr(current, field) != getattr(expected_page, field)
                    for field in ("name", "content", "description", "tags", "related", "revision")
                ):
                    raise RuntimeError(f"页面「{name}」在整理期间发生变化，请重新读取后更新")
            revision = max(current.revision if current else 0, state["page_revisions"].get(name, 0)) + 1
            saved = self._save_page(name, content, description, tags, related, source_ids=source_ids, status=status, batch_id=batch_id, revision=revision)
            if name in state["deleted_pages"]:
                state["deleted_pages"].pop(name)
                self.provenance.write(state)
            return saved

    def _save_page(
        self,
        name: str,
        content: str,
        description: str = "",
        tags: list[str] = None,
        related: list[str] = None,
        *, source_ids: list[str] | None = None, status: str | None = None, batch_id: str | None = None, revision: int,
    ) -> WikiPage:
        """
        新建或更新 Wiki 页面（upsert 语义）

        - 页面不存在 → 新建，created_at = now
        - 页面已存在 → 更新内容，保留 created_at，updated_at = now

        同时更新 index.json → 重建 index.md → 追加 log.md
        """
        filepath = self._page_path(name)
        self.index.all_entries()
        existing = self.get_page(name)
        now = datetime.now()

        if existing:
            page = WikiPage(
                name=name,
                content=content,
                description=description,
                tags=existing.tags if tags is None else list(dict.fromkeys(tags)),
                related=existing.related if related is None else list(dict.fromkeys(related)),
                created_at=existing.created_at,
                updated_at=now,
            )
            action = "update"
        else:
            page = WikiPage(
                name=name,
                content=content,
                description=description,
                tags=list(dict.fromkeys(tags or [])),
                related=list(dict.fromkeys(related or [])),
                created_at=now,
                updated_at=now,
            )
            action = "write"

        page.revision = revision
        page.source_ids = list(dict.fromkeys(source_ids if source_ids is not None else existing.source_ids if existing else []))
        page.status = status or ("ready" if page.source_ids else "legacy")
        page.applied_batches = list(existing.applied_batches if existing else [])
        if batch_id:
            page.applied_batches.append(batch_id)

        # 写入文件
        atomic_write(filepath, page.to_markdown())

        # 更新索引
        self.index.upsert(page)
        self.index.append_log(action, name, description)
        self.invalidate_cache()

        logger.info(f"WikiMemory.save_page [{action}]: {name}")
        return page

    def append_page(
        self,
        name: str,
        content: str,
        tags: list[str] = None,
        related: list[str] = None,
    ) -> WikiPage:
        """
        追加内容到已有页面末尾。
        页面不存在时自动创建（等同于 save_page）。
        tags/related 为空时保留已有值。
        """
        with self._lock:
            existing = self.get_page(name)
            if not existing:
                return self.save_page(name=name, content=content, tags=tags or [], related=related or [])

            new_content = existing.content.rstrip() + "\n\n" + content.strip()
            merged_tags = list(dict.fromkeys(existing.tags + (tags or [])))
            merged_related = list(dict.fromkeys(existing.related + (related or [])))
            return self.save_page(
                name=name,
                content=new_content,
                description=existing.description,
                tags=merged_tags,
                related=merged_related,
                source_ids=existing.source_ids,
            )

    def fuzzy_find_name(self, name: str) -> Optional[str]:
        """
        模糊匹配页面名（忽略大小写和空格）。
        找到返回 index 中的真实页面名，找不到返回 None。
        """
        normalized = name.lower().replace(" ", "").replace("_", "")
        for entry in self.index.all_entries():
            real_name = entry["name"]
            if real_name.lower().replace(" ", "").replace("_", "") == normalized:
                return real_name
        return None

    def get_page(self, name: str) -> Optional[WikiPage]:
        """读取指定页面，不存在返回 None"""
        try:
            filepath = self._page_path(name)
        except ValueError:
            return None
        if not filepath.exists():
            return None
        try:
            text = filepath.read_text(encoding="utf-8")
            return WikiPage.from_markdown(text, fallback_name=name)
        except Exception as e:
            logger.error(f"读取页面失败: {name}, {e}")
            return None

    def delete_page(self, name: str) -> bool:
        """删除页面及其索引条目"""
        with self._lock:
            filepath = self._page_path(name)
            if not filepath.exists():
                logger.warning(f"页面不存在: {name}")
                return False

            self.index.all_entries()
            state = self.provenance.read()
            page = self.get_page(name)
            # 删除后的版本下限继续保留，重建同名页面也不能复用旧版本。
            if page:
                state["page_revisions"][name] = max(page.revision, state["page_revisions"].get(name, 0))
            state["deleted_pages"][name] = datetime.now().isoformat()
            state["repairs"].pop(name, None)
            state["baselines"].pop(name, None)
            for source in state["sources"].values():
                if source.get("page_name") == name:
                    source["state"] = "withdrawn"
                    source.pop("body", None)
                    source.pop("diff", None)
            self.provenance.write(state)
            filepath.unlink()
            self.index.remove(name)
            self.index.append_log("delete", name)
            logger.info(f"WikiMemory.delete_page: {name}")
            self.invalidate_cache()
            return True

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def list_pages(self, *, include_unavailable: bool = False) -> list[WikiPage]:
        """列出所有页面（从 pages/ 目录扫描）"""
        pages = []
        with self._lock:
            deleted = self.provenance.read()["deleted_pages"]
        for md_file in sorted(self.pages_dir.glob("*.md")):
            if md_file.stem in deleted:
                continue
            page = self.get_page(md_file.stem)
            if page and (include_unavailable or self.retrievable(page)):
                pages.append(page)
        return pages

    def search_pages(self, query: str) -> list[WikiPage]:
        """
        关键词全文搜索（name + description + content + tags）
        返回匹配的页面列表，按相关性粗排（name > description > content）
        """
        query_lower = query.lower()
        name_hits = []
        desc_hits = []
        content_hits = []

        for page in self.list_pages():
            if query_lower in page.name.lower():
                name_hits.append(page)
            elif query_lower in page.description.lower() or any(
                query_lower in t.lower() for t in page.tags
            ):
                desc_hits.append(page)
            elif query_lower in page.content.lower():
                content_hits.append(page)

        return name_hits + desc_hits + content_hits

    def select_relevant_pages(
        self,
        query: str,
        messages: list[dict],
        top_k: int = 3,
    ) -> list[WikiPage]:
        """
        基于 LLM 的智能页面选择

        两阶段筛选：
        1. 关键词初筛：用 search_pages 获取候选页面
        2. LLM 精选：用 WikiPageSelector 做语义匹配，选出 top_k 个最相关页面

        Args:
            query: 用户当前消息（用于关键词初筛和 LLM 精选）
            messages: 主 agent 的完整 messages 列表（继承对话上下文）
            top_k: 返回的最相关页面数量，默认 3

        Returns:
            按相关性排序的 WikiPage 列表

        用法：
            wiki = WikiMemory.for_workspace(memory_dir)
            pages = wiki.select_relevant_pages(
                query="如何配置 LLM provider?",
                messages=agent.messages,
            )
        """
        from qrclaw.memory.wiki.selection import WikiPageSelector

        # 第一阶段：关键词初筛，获取候选页面
        keyword_candidates = self.search_pages(query)
        candidates = (keyword_candidates or self.list_pages())[:20]
        if not candidates:
            return []

        # 第二阶段：LLM 精选
        selector = WikiPageSelector(self)
        selection_result = selector.select(query, messages, candidates=candidates)

        if not selection_result.selected:
            logger.debug("select_relevant_pages: LLM 精选无结果，返回关键词匹配结果")
            return keyword_candidates[:top_k]

        # 根据 LLM 选择结果获取 WikiPage 对象
        selected_pages = []
        for selected in selection_result.selected[:top_k]:
            page = self.get_page(selected.name)
            if page and self.retrievable(page) and page.name in {p.name for p in candidates}:
                selected_pages.append(page)

        logger.debug(
            f"select_relevant_pages: 关键词候选 {len(candidates)} 个，LLM 选中 {len(selected_pages)} 个"
        )
        return selected_pages or keyword_candidates[:top_k]

    def page_exists(self, name: str) -> bool:
        """检查页面是否存在"""
        try:
            return self._page_path(name).exists()
        except ValueError:
            return False

    # ── system prompt 注入 ────────────────────────────────────────────────────

    def load_index(self) -> str:
        """
        返回 index.md 全文，供注入 system prompt。

        Agent 通过这个了解 Wiki 全貌，决定新建还是更新页面。
        """
        return "# Wiki 索引\n\n" + "\n".join(f"- {p.name}：{p.description}" for p in self.list_pages())

    def retrievable(self, page: WikiPage) -> bool:
        """来源撤销或整理失败期间不向 Agent 暴露旧正文和旧摘要。"""
        with self._lock:
            state = self.provenance.read()
            return (page.status in ("ready", "legacy") and page.name not in state["deleted_pages"]
                    and self.provenance.current(page.source_ids)
                    and state["repairs"].get(page.name, {}).get("status") not in ("pending", "processing", "failed"))

    def backlinks(self, name: str) -> list[str]:
        """计算反向引用，章节和别名沿用统一解析规则。"""
        return [p.name for p in self.list_pages(include_unavailable=True)
                if name in p.related + wiki_links(p.content) and p.name != name]

    @property
    def repair_worker(self):
        """按工作空间复用单个可恢复整理工作器。"""
        if self._repair_worker is None:
            from qrclaw.memory.wiki.repair import WikiRepairWorker
            self._repair_worker = WikiRepairWorker(self)
        return self._repair_worker

    def invalidate_cache(self) -> None:
        """记忆变化后使下一轮系统提示读取新索引。"""
        from qrclaw.memory.context.context_manager import get_context_manager
        try:
            get_context_manager().invalidate_cache()
        except RuntimeError:
            pass

    # ── 维护 ──────────────────────────────────────────────────────────────────

    def rebuild_index(self) -> None:
        """从 pages/ 目录完整重建 index.json 和 index.md（修复用）"""
        with self._lock:
            self.index.rebuild_from_pages_dir()
        logger.info("WikiMemory: 重建索引完成")

    def clear_all(self) -> None:
        """清空所有页面和索引（开发/测试用）"""
        with self._lock:
            for md_file in self.pages_dir.glob("*.md"):
                md_file.unlink()
            self.index.rebuild_from_pages_dir()
        logger.info("WikiMemory: 已清空所有页面")

    def stats(self) -> dict:
        """返回统计信息"""
        entries = self.index.all_entries()
        return {
            "total_pages": len(entries),
            "memory_dir": str(self.memory_dir),
        }
