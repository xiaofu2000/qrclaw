"""
WikiPage —— LLM Wiki 页面数据类

替代原有的 MemoryFile + MemoryType 四分类体系。
每个页面是一个独立的 Markdown 文件，存放在 pages/ 目录下，
页面间通过 [[页面名]] 和 related 字段互相关联。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.wiki.page")


@dataclass
class WikiPage:
    """
    Wiki 页面数据类

    对应磁盘上 pages/<name>.md 文件，frontmatter 格式：

        ---
        name: qrclaw架构
        description: QRClaw Agent 的整体执行架构
        tags: [架构, 核心]
        related: [记忆系统, Router节点]
        created_at: 2026-04-10T00:00:00
        updated_at: 2026-04-10T12:00:00
        ---

        # qrclaw架构

        正文内容，可用 [[页面名]] 建立链接...
    """

    name: str
    content: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def filename(self) -> str:
        """文件名，直接用 name，保留中文可读性"""
        return f"{self.name}.md"

    # ── 序列化 ────────────────────────────────────────────────────────────────

    def to_markdown(self) -> str:
        """序列化为带 frontmatter 的 Markdown 文本"""
        tags_str = ", ".join(self.tags) if self.tags else ""
        related_str = ", ".join(self.related) if self.related else ""

        lines = ["---", f"name: {self.name}", f"description: {self.description}"]

        if tags_str:
            lines.append(f"tags: [{tags_str}]")
        else:
            lines.append("tags: []")

        if related_str:
            lines.append(f"related: [{related_str}]")
        else:
            lines.append("related: []")

        lines += [
            f"created_at: {self.created_at.isoformat()}",
            f"updated_at: {self.updated_at.isoformat()}",
            "---",
            "",
            self.content,
        ]
        return "\n".join(lines)

    # ── 反序列化 ──────────────────────────────────────────────────────────────

    @classmethod
    def from_markdown(cls, text: str, fallback_name: str = "") -> Optional["WikiPage"]:
        """
        从 Markdown 文本解析 WikiPage。
        支持有 frontmatter 和无 frontmatter 两种情况。
        """
        if not text or not text.strip():
            return None

        match = re.match(r"^---\n(.*?)\n---\n?(.*)", text.strip(), re.DOTALL)
        if not match:
            # 无 frontmatter，用文件名作为 name
            return cls(
                name=fallback_name or "未命名页面",
                content=text.strip(),
                description="",
            )

        fm_text = match.group(1)
        content = match.group(2).strip()

        fields = cls._parse_frontmatter(fm_text)

        try:
            return cls(
                name=fields.get("name") or fallback_name or "未命名页面",
                content=content,
                description=fields.get("description", ""),
                tags=fields.get("tags", []),
                related=fields.get("related", []),
                created_at=cls._parse_dt(fields.get("created_at")),
                updated_at=cls._parse_dt(fields.get("updated_at")),
            )
        except Exception as e:
            logger.error(f"解析 WikiPage 失败: {e}", exc_info=True)
            return None

    @staticmethod
    def _parse_frontmatter(text: str) -> dict:
        """解析 frontmatter 文本为字典"""
        result = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, _, raw = line.partition(":")
            key = key.strip()
            raw = raw.strip()

            if key in ("tags", "related"):
                # 解析 [a, b, c] 格式
                inner = raw.strip("[]")
                result[key] = [v.strip() for v in inner.split(",") if v.strip()] if inner else []
            else:
                result[key] = raw

        return result

    @staticmethod
    def _parse_dt(value: Optional[str]) -> datetime:
        """解析 ISO 8601 日期时间，失败返回当前时间"""
        if not value:
            return datetime.now()
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now()

    # ── 工具方法 ──────────────────────────────────────────────────────────────

    def to_index_entry(self) -> dict:
        """生成 index.json 的条目格式"""
        return {
            "name": self.name,
            "file": f"pages/{self.filename}",
            "description": self.description,
            "tags": self.tags,
            "related": self.related,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"WikiPage(name={self.name!r}, tags={self.tags}, related={self.related})"
