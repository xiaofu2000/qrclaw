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

from qrclaw.logger import get_logger
from qrclaw.memory.wiki.index import IndexManager
from qrclaw.memory.wiki.page import WikiPage

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
        self.index = IndexManager(self.memory_dir)

        self.pages_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"WikiMemory 初始化: {self.memory_dir}")

    # ── 核心 CRUD ─────────────────────────────────────────────────────────────

    def save_page(
        self,
        name: str,
        content: str,
        description: str = "",
        tags: list[str] = None,
        related: list[str] = None,
    ) -> WikiPage:
        """
        新建或更新 Wiki 页面（upsert 语义）

        - 页面不存在 → 新建，created_at = now
        - 页面已存在 → 更新内容，保留 created_at，updated_at = now

        同时更新 index.json → 重建 index.md → 追加 log.md
        """
        tags = tags or []
        related = related or []

        existing = self.get_page(name)
        now = datetime.now()

        if existing:
            page = WikiPage(
                name=name,
                content=content,
                description=description or existing.description,
                tags=tags or existing.tags,
                related=related or existing.related,
                created_at=existing.created_at,
                updated_at=now,
            )
            action = "update"
        else:
            page = WikiPage(
                name=name,
                content=content,
                description=description,
                tags=tags,
                related=related,
                created_at=now,
                updated_at=now,
            )
            action = "write"

        # 写入文件
        filepath = self.pages_dir / page.filename
        filepath.write_text(page.to_markdown(), encoding="utf-8")

        # 更新索引
        self.index.upsert(page)
        self.index.append_log(action, name, description)

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
        filepath = self.pages_dir / f"{name}.md"
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
        filepath = self.pages_dir / f"{name}.md"
        if not filepath.exists():
            logger.warning(f"页面不存在: {name}")
            return False

        filepath.unlink()
        self.index.remove(name)
        self.index.append_log("delete", name)
        logger.info(f"WikiMemory.delete_page: {name}")
        return True

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def list_pages(self) -> list[WikiPage]:
        """列出所有页面（从 pages/ 目录扫描）"""
        pages = []
        for md_file in sorted(self.pages_dir.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            page = WikiPage.from_markdown(text, fallback_name=md_file.stem)
            if page:
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
        candidates = self.search_pages(query)
        if not candidates:
            logger.debug(f"select_relevant_pages: 关键词初筛无候选页面 (query={query})")
            return []

        # 第二阶段：LLM 精选
        selector = WikiPageSelector(self)
        selection_result = selector.select(query, messages)

        if not selection_result.selected:
            logger.debug("select_relevant_pages: LLM 精选无结果，返回关键词匹配结果")
            return candidates[:top_k]

        # 根据 LLM 选择结果获取 WikiPage 对象
        selected_pages = []
        for selected in selection_result.selected[:top_k]:
            page = self.get_page(selected.name)
            if page:
                selected_pages.append(page)

        logger.debug(
            f"select_relevant_pages: 关键词候选 {len(candidates)} 个，LLM 选中 {len(selected_pages)} 个"
        )
        return selected_pages

    def page_exists(self, name: str) -> bool:
        """检查页面是否存在"""
        return (self.pages_dir / f"{name}.md").exists()

    # ── system prompt 注入 ────────────────────────────────────────────────────

    def load_index(self) -> str:
        """
        返回 index.md 全文，供注入 system prompt。

        Agent 通过这个了解 Wiki 全貌，决定新建还是更新页面。
        """
        return self.index.load_index_md()

    # ── 维护 ──────────────────────────────────────────────────────────────────

    def rebuild_index(self) -> None:
        """从 pages/ 目录完整重建 index.json 和 index.md（修复用）"""
        self.index.rebuild_from_pages_dir()
        logger.info("WikiMemory: 重建索引完成")

    def clear_all(self) -> None:
        """清空所有页面和索引（开发/测试用）"""
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
