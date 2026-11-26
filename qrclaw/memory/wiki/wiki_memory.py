"""
WikiMemory —— LLM Wiki 记忆管理器

采用 LLM Wiki 架构的长期记忆：
- 所有页面平铺在 pages/ 目录
- index.json 作为结构化索引（代码用）
- index.md 作为人类可读目录
- log.md 记录操作历史

核心操作：
- save_page：新建或更新页面（upsert 语义）
- get_page：读取指定页面
- list_pages：列出所有页面（单次状态读取）
- search_pages：中英混合 BM25 + 字段加权全文搜索
- delete_page：删除页面
- load_index：生成可检索页面的索引，供 system prompt 注入（带轻量内存缓存）
"""

from __future__ import annotations

from datetime import datetime
import threading
from pathlib import Path
from typing import Optional
from filelock import FileLock

from qrclaw.home import qrclaw_home
from qrclaw.logger import get_logger
from qrclaw.memory.wiki.index import IndexManager, atomic_write
from qrclaw.memory.wiki.page import WikiPage, wiki_links
from qrclaw.memory.wiki.search_ranker import WeightedField, rank_documents

logger = get_logger("qrclaw.memory.wiki.wiki_memory")

AGENTS_ROOT = qrclaw_home() / "agents"
_APPLIED_BATCHES_LIMIT = 200
_SELECTOR_PREFILTER_LIMIT = 30

# 按 memory_dir 缓存实例，同一目录永远返回同一个实例
_instances: dict[Path, "WikiMemory"] = {}
# 多个会话可能同时第一次访问同一目录；不加锁会各建一个实例，实例上的锁就互相看不见。
_instances_lock = threading.RLock()


def _path_sig(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


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
        with _instances_lock:
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
        self._dream_worker = None
        self._history = None
        self._store_versions = None
        self._index_cache: tuple[tuple, str] | None = None

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
        - 页面已存在 → 先将旧页面存入单页历史版本，再更新内容，保留 created_at，updated_at = now

        同时更新 index.json → 重建 index.md → 追加 log.md
        """
        filepath = self._page_path(name)
        self.index.all_entries()
        existing = self.get_page(name)
        now = datetime.now()

        if existing:
            self.history.save_snapshot(existing)
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
        batches = list(existing.applied_batches if existing else [])
        if batch_id:
            batches.append(batch_id)
        if len(batches) > _APPLIED_BATCHES_LIMIT:
            batches = batches[-_APPLIED_BATCHES_LIMIT:]
        page.applied_batches = batches

        # 写入文件
        atomic_write(filepath, page.to_markdown())

        # 更新索引
        self.index.upsert(page)
        self.index.append_log(action, name, description)
        self.invalidate_cache()

        logger.info(f"WikiMemory.save_page [{action}]: {name}")
        return page

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
        """删除页面及其索引条目，并在删除前保存单页历史版本"""
        with self._lock:
            filepath = self._page_path(name)
            if not filepath.exists():
                logger.warning(f"页面不存在: {name}")
                return False

            self.index.all_entries()
            page = self.get_page(name)
            if page:
                self.history.save_snapshot(page)
            state = self.provenance.read()
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

    def _retrievable_with_state(self, page: WikiPage, state: dict) -> bool:
        """复用已读取的来源快照校验页面是否可暴露给 Agent。"""
        if page.status not in ("ready", "legacy"):
            return False
        if page.name in state["deleted_pages"]:
            return False
        sources = state["sources"]
        if not all(sources.get(key, {}).get("state") == "valid" for key in page.source_ids):
            return False
        if state["repairs"].get(page.name, {}).get("status") in ("pending", "processing", "failed"):
            return False
        return True

    def list_pages(self, *, include_unavailable: bool = False) -> list[WikiPage]:
        """列出所有页面（从 pages/ 目录扫描），单次调用只读一次状态快照。"""
        pages = []
        with self._lock:
            state = self.provenance.read_readonly()
            deleted = state["deleted_pages"]
            for md_file in sorted(self.pages_dir.glob("*.md")):
                if md_file.stem in deleted:
                    continue
                page = self.get_page(md_file.stem)
                if page and (include_unavailable or self._retrievable_with_state(page, state)):
                    pages.append(page)
        return pages

    @staticmethod
    def _page_search_fields(page: WikiPage) -> tuple[WeightedField, ...]:
        return (
            WeightedField(page.name, weight=8.0, exact_boost=20.0),
            WeightedField(" ".join(page.tags), weight=5.0, exact_boost=8.0),
            WeightedField(page.description, weight=3.0, exact_boost=6.0),
            WeightedField(page.content, weight=1.0, exact_boost=3.0),
        )

    def search_pages(self, query: str) -> list[WikiPage]:
        """中英混合 BM25 + 字段加权搜索（name > tags > description > content）。"""
        pages = self.list_pages()
        if not query or not query.strip() or not pages:
            return []
        ranked = rank_documents(query, pages, self._page_search_fields)
        return [entry.item for entry in ranked]

    def select_relevant_pages(
        self,
        query: str,
        messages: list[dict],
        top_k: int = 3,
    ) -> list[WikiPage]:
        """
        基于 LLM 的智能页面选择（供 Plan 模式使用）。

        当候选目录超过 30 页时，先使用本地中英混合 BM25 粗筛 Top 30 再交给模型精选，
        防止目录增长后撑爆上下文与调用成本。
        """
        from qrclaw.memory.wiki.selection import WikiPageSelector

        candidates = self.list_pages()
        if not candidates or top_k < 1:
            return []

        query_folded = query.casefold()
        mentioned = [p.name for p in candidates if len(p.name) > 1 and p.name.casefold() in query_folded]

        selector_candidates = candidates
        if len(candidates) > _SELECTOR_PREFILTER_LIMIT:
            ranked = [r.item for r in rank_documents(query, candidates, self._page_search_fields, limit=_SELECTOR_PREFILTER_LIMIT)]
            picked_names = {p.name for p in ranked}
            # 确保查询中点名的页面以及不足 30 页时的补齐页都在候选集中
            selector_candidates = list(ranked)
            for p in candidates:
                if p.name in mentioned and p.name not in picked_names:
                    selector_candidates.insert(0, p)
                    picked_names.add(p.name)
            if len(selector_candidates) < _SELECTOR_PREFILTER_LIMIT:
                for p in candidates:
                    if p.name not in picked_names:
                        selector_candidates.append(p)
                        picked_names.add(p.name)
                        if len(selector_candidates) >= _SELECTOR_PREFILTER_LIMIT:
                            break
            selector_candidates = selector_candidates[:_SELECTOR_PREFILTER_LIMIT]

        selected = []
        if not any(name.casefold() == query_folded for name in mentioned):
            selected = WikiPageSelector(self).select(query, messages, candidates=selector_candidates).selected
        names = mentioned + [page.name for page in selected if page.relevance >= 0.6]

        # 选页期间页面可能被撤销，返回前重新验证。
        selected_pages = []
        candidate_names = {p.name for p in candidates}
        seen = set()
        with self._lock:
            state = self.provenance.read_readonly()
            for name in names:
                if name in seen:
                    continue
                seen.add(name)
                page = self.get_page(name)
                if page and self._retrievable_with_state(page, state) and name in candidate_names:
                    selected_pages.append(page)
                if len(selected_pages) >= top_k:
                    break

        logger.debug(f"select_relevant_pages: 目录页面 {len(candidates)} 个，选中 {len(selected_pages)} 个")
        return selected_pages or self.search_pages(query)[:top_k]

    def page_exists(self, name: str) -> bool:
        """检查页面是否存在"""
        try:
            return self._page_path(name).exists()
        except ValueError:
            return False

    # ── system prompt 注入 ────────────────────────────────────────────────────

    def _index_signature(self) -> tuple:
        return (
            _path_sig(self.pages_dir),
            _path_sig(self.memory_dir / "index.json"),
            _path_sig(self.provenance.path),
        )

    def load_index(self) -> str:
        """
        生成注入 system prompt 的索引：每个可检索页面一行「名称：描述」。

        命中轻量内存缓存时无需扫描磁盘；在记忆变化或文件签名改变时自动失效重建。
        """
        sig = self._index_signature()
        cached = self._index_cache
        if cached is not None and cached[0] == sig:
            return cached[1]
        pages = self.list_pages()
        text = "# Wiki 索引\n\n" + "\n".join(f"- {p.name}：{p.description}" for p in pages)
        self._index_cache = (self._index_signature(), text)
        return text

    def retrievable(self, page: WikiPage) -> bool:
        """来源撤销或整理失败期间不向 Agent 暴露旧正文和旧摘要。"""
        with self._lock:
            state = self.provenance.read_readonly()
            return self._retrievable_with_state(page, state)

    def backlinks(self, name: str) -> list[str]:
        """计算反向引用，章节和别名沿用统一解析规则。"""
        return [p.name for p in self.list_pages(include_unavailable=True)
                if name in p.related + wiki_links(p.content) and p.name != name]

    @property
    def history(self):
        """单页不可变历史版本管理器。"""
        if self._history is None:
            from qrclaw.memory.wiki.history import WikiPageHistory
            self._history = WikiPageHistory(self)
        return self._history

    @property
    def store_versions(self):
        """整库归档与版本回退管理器。"""
        if self._store_versions is None:
            from qrclaw.memory.wiki.store_versions import WikiStoreVersions
            self._store_versions = WikiStoreVersions(self)
        return self._store_versions

    @property
    def repair_worker(self):
        """按工作空间复用单个可恢复整理工作器。"""
        if self._repair_worker is None:
            from qrclaw.memory.wiki.repair import WikiRepairWorker
            self._repair_worker = WikiRepairWorker(self)
        return self._repair_worker

    @property
    def dream_worker(self):
        """按工作空间复用单个记忆做梦（整理）工作器。"""
        if self._dream_worker is None:
            from qrclaw.memory.wiki.dream import WikiDreamWorker
            self._dream_worker = WikiDreamWorker(self)
        return self._dream_worker

    def invalidate_cache(self) -> None:
        """记忆变化后使本地索引缓存及下一轮系统提示缓存同时失效。"""
        self._index_cache = None
        from qrclaw.memory.context.runtime.manager import get_context_manager
        try:
            get_context_manager().invalidate_cache()
        except RuntimeError:
            pass

    # ── 维护 ──────────────────────────────────────────────────────────────────

    def rebuild_index(self) -> None:
        """从 pages/ 目录完整重建 index.json 和 index.md（修复用）"""
        with self._lock:
            self.index.rebuild_from_pages_dir()
        self.invalidate_cache()
        logger.info("WikiMemory: 重建索引完成")
