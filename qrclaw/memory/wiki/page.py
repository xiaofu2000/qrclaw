"""Wiki 页面的正文、来源引用和版本，兼容旧 Markdown 文件。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
import hashlib
from typing import Optional

import yaml


def wiki_links(content: str) -> list[str]:
    """将普通、别名和章节链接统一为页面名称，去重并保持顺序。"""
    return list(dict.fromkeys(name for link in re.findall(r"\[\[([^\]]+)\]\]", content)
                             if (name := link.split("|", 1)[0].split("#", 1)[0].strip())))


@dataclass
class WikiPage:
    """一个 Markdown 页面；来源正文单独存储，页面只保存来源 ID。"""

    name: str
    content: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    revision: int = 0
    source_ids: list[str] = field(default_factory=list)
    status: str = "legacy"
    applied_batches: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        """保留现有页面文件名约定。"""
        return f"{self.name}.md"

    def to_markdown(self) -> str:
        """使用安全 YAML 序列化，避免中文标点、冒号和列表内容损坏元数据。"""
        data = asdict(self)
        data.pop("content")
        for key in ("created_at", "updated_at"):
            data[key] = data[key].isoformat()
        return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n\n" + self.content

    @classmethod
    def from_markdown(cls, text: str, fallback_name: str = "") -> Optional["WikiPage"]:
        """读取新旧页面；没有元数据的旧文件仍可查看，不推测来源。"""
        if not text or not text.strip():
            return None
        match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)", text.strip(), re.DOTALL)
        fields = cls._parse_frontmatter(match.group(1)) if match else {}
        content = match.group(2).lstrip("\r\n") if match else text.strip()
        for key in ("tags", "related", "source_ids", "applied_batches"):
            value = fields.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"Wiki 页面字段 {key} 必须是字符串列表")
        revision = fields.get("revision", int(hashlib.sha256(text.encode()).hexdigest()[:12], 16))
        if type(revision) is not int or revision < 0:
            raise ValueError("Wiki 页面版本无效")
        status = fields.get("status", "legacy")
        if status not in ("legacy", "ready", "pending", "processing", "failed", "inactive"):
            raise ValueError("Wiki 页面状态无效")
        return cls(name=str(fallback_name or fields.get("name") or "未命名页面"), content=content,
                   description=str(fields.get("description") or ""), tags=fields.get("tags", []),
                   related=fields.get("related", []), created_at=cls._parse_dt(fields.get("created_at")),
                   updated_at=cls._parse_dt(fields.get("updated_at")), revision=revision,
                   source_ids=fields.get("source_ids", []), status=status,
                   applied_batches=fields.get("applied_batches", []))

    @staticmethod
    def _parse_frontmatter(text: str) -> dict:
        """优先解析 YAML；仅旧格式允许未引用的冒号和简单逗号列表。"""
        try:
            fields = yaml.safe_load(text)
            if not isinstance(fields, dict):
                raise ValueError("Wiki 元数据必须是对象")
            return fields
        except yaml.YAMLError:
            if re.search(r"^(revision|source_ids|status|applied_batches):", text, re.MULTILINE):
                raise ValueError("Wiki 元数据损坏")
            fields = {}
            for line in text.splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    value = value.strip()
                    fields[key.strip()] = ([item.strip().strip("\"'") for item in value.strip("[]").split(",") if item.strip()]
                                           if key.strip() in ("tags", "related") else value)
            return fields

    @staticmethod
    def _parse_dt(value) -> datetime:
        """旧文件缺少时间时使用固定值，避免每次读取产生虚假的版本变化。"""
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return datetime(1970, 1, 1)

    def to_index_entry(self) -> dict:
        """索引包含状态和版本，检索可先排除不可信的旧正文。"""
        data = asdict(self)
        data.pop("content")
        data.pop("applied_batches")
        data["file"] = f"pages/{self.filename}"
        data["source_count"] = len(self.source_ids)
        for key in ("created_at", "updated_at"):
            data[key] = data[key].isoformat()
        return data
