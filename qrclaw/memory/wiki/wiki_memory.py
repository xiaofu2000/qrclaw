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
