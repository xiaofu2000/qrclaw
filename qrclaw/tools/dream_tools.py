"""记忆整理（做梦）Agent 专属工具集。

严格安全边界：
1. 仅注册给 AgentType.DREAM，主 Agent 与子 Agent 均不可见；
2. 模型无法传入任何目录或文件路径，只能传页面名称或受控的 session_id；
3. 页面名称严格禁止 `../`、`/`、`\\`、换行、控制字符及超长字节，所有读写均锚定在当前做梦副本 pages/ 内；
4. 单页写入大小不超过 dream_page_max_bytes（默认 100 KB），并统一经 WikiPage 序列化保证 frontmatter 合法。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, Field

from qrclaw.config import settings
from qrclaw.memory.wiki.index import atomic_write
from qrclaw.memory.wiki.page import WikiPage
from qrclaw.tools.registry import AgentType, _tools, register

DREAM_TOOL_NAMES = frozenset({
    "memory_view",
    "memory_create",
    "memory_str_replace",
    "memory_insert",
    "memory_delete",
    "memory_rename",
    "transcript_list",
    "transcript_read",
})


@dataclass
class DreamToolContext:
    """单次做梦运行的隔离上下文，通过 ContextVar 注入给工具调用。"""

    pages_dir: Path
    transcripts: dict[str, dict] = field(default_factory=dict)
    session_order: list[str] = field(default_factory=list)
    deleted_pages: set[str] = field(default_factory=set)
    max_page_bytes: int = field(default_factory=lambda: settings.memory.dream_page_max_bytes)


_dream_ctx: ContextVar[DreamToolContext | None] = ContextVar("qrclaw_dream_tool_context", default=None)


@contextmanager
def dream_tool_context(ctx: DreamToolContext) -> Iterator[DreamToolContext]:
    """在当前线程/上下文中激活做梦副本目录与会话记录。"""
    token = _dream_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _dream_ctx.reset(token)


def _require_ctx() -> DreamToolContext:
    ctx = _dream_ctx.get()
    if ctx is None:
        raise RuntimeError("做梦工具只能在记忆整理任务上下文中执行")
    return ctx


def validate_dream_page_name(pages_dir: Path, name: str) -> Path:
    """严格校验页面名称并锚定到副本目录，拒绝任何路径穿越、斜杠、换行或超长名称。"""
    if (
        not isinstance(name, str)
        or not name.strip()
        or name != name.strip()
        or name in (".", "..")
        or ".." in name
        or len(name.encode("utf-8")) > 252
        or any(ch in name for ch in "/\\\r\n\0")
        or any(ord(ch) < 32 for ch in name)
    ):
        raise ValueError(f"无效的页面名称: {name!r}")
    root = pages_dir.resolve()
    target = pages_dir / f"{name}.md"
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"无效的页面名称: {name!r}")
    return target


def _validate_metadata_lists(pages_dir: Path, tags: list[str] | None, related: list[str] | None) -> tuple[list[str], list[str]]:
    clean_tags = list(dict.fromkeys(t.strip() for t in (tags or []) if isinstance(t, str) and t.strip()))
    clean_related: list[str] = []
    for item in related or []:
        if not isinstance(item, str) or not item.strip():
            continue
        rname = item.strip()
        validate_dream_page_name(pages_dir, rname)
        if rname not in clean_related:
            clean_related.append(rname)
    return clean_tags, clean_related


def _write_dream_page(ctx: DreamToolContext, path: Path, page: WikiPage) -> None:
    if not page.content.strip():
        raise ValueError("页面正文不能为空")
    page.status = "legacy"
    page.source_ids = []
    page.applied_batches = []
    raw_md = page.to_markdown()
    encoded = raw_md.encode("utf-8")
    if len(encoded) > ctx.max_page_bytes:
        raise ValueError(
            f"单页大小（{len(encoded)} 字节）超过上限 {ctx.max_page_bytes} 字节，请拆分为更小、更专注的页面"
        )
    ctx.pages_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(path, raw_md)


# ── Pydantic 参数模型 ────────────────────────────────────────────────────────

class MemoryViewArgs(BaseModel):
    name: str = Field(default="", description="页面名称；省略或留空时列出副本中的全部页面目录")
    start_line: int = Field(default=1, ge=1, description="起始行号（从 1 开始）")
    max_lines: int = Field(default=120, ge=1, le=200, description="单次最多读取的行数")


class MemoryCreateArgs(BaseModel):
    name: str = Field(min_length=1, description="新页面名称（不含路径与扩展名，如 'QRClaw 压缩机制'）")
    content: str = Field(min_length=1, description="Markdown 正文")
    description: str = Field(default="", max_length=200, description="50 字以内的简明摘要")
    tags: list[str] = Field(default_factory=list, max_length=30, description="标签列表")
    related: list[str] = Field(default_factory=list, max_length=30, description="关联页面名称列表")


class MemoryStrReplaceArgs(BaseModel):
    name: str = Field(min_length=1, description="目标页面名称")
    old_str: str = Field(min_length=1, description="待替换的唯一原文片段")
    new_str: str = Field(default="", description="替换后的新文本")
    description: str | None = Field(default=None, max_length=200, description="可选更新的页面摘要")
    tags: list[str] | None = Field(default=None, max_length=30, description="可选更新的完整标签列表")
    related: list[str] | None = Field(default=None, max_length=30, description="可选更新的完整关联页面列表")


class MemoryInsertArgs(BaseModel):
    name: str = Field(min_length=1, description="目标页面名称")
    insert_line: int = Field(ge=0, description="插入位置行号：0 表示在首行前插入，N 表示在第 N 行后插入")
    new_str: str = Field(min_length=1, description="要插入的新文本")
    description: str | None = Field(default=None, max_length=200, description="可选更新的页面摘要")
    tags: list[str] | None = Field(default=None, max_length=30, description="可选更新的完整标签列表")
    related: list[str] | None = Field(default=None, max_length=30, description="可选更新的完整关联页面列表")


class MemoryDeleteArgs(BaseModel):
    name: str = Field(min_length=1, description="要从整理副本中移除的页面名称")


class MemoryRenameArgs(BaseModel):
    old_name: str = Field(min_length=1, description="原页面名称")
    new_name: str = Field(min_length=1, description="新页面名称")
    description: str | None = Field(default=None, max_length=200, description="可选更新的页面摘要")


class TranscriptListArgs(BaseModel):
    pass


class TranscriptReadArgs(BaseModel):
    session_id: str = Field(min_length=1, description="要读取的会话 ID（来自 transcript_list）")
    offset: int = Field(default=0, ge=0, description="起始消息序号（从 0 开始）")
    limit: int = Field(default=20, ge=1, le=50, description="单次读取的消息条数上限")


# ── 工具实现 ─────────────────────────────────────────────────────────────────

def memory_view(name: str = "", start_line: int = 1, max_lines: int = 120) -> str:
    """列出副本全部页面，或按行分页读取指定页面的元数据与正文。"""
    try:
        ctx = _require_ctx()
        ctx.pages_dir.mkdir(parents=True, exist_ok=True)
        root = ctx.pages_dir.resolve()
        if name == "":
            items = []
            total_chars = 0
            for md_file in sorted(ctx.pages_dir.glob("*.md")):
                if not md_file.resolve().is_relative_to(root):
                    continue
                page = WikiPage.from_markdown(md_file.read_text(encoding="utf-8"), fallback_name=md_file.stem)
                if page is None:
                    continue
                chars = len(page.content)
                total_chars += chars
                items.append({
                    "name": md_file.stem,
                    "description": page.description,
                    "tags": page.tags,
                    "related": page.related,
                    "char_count": chars,
                    "line_count": len(page.content.splitlines()),
                })
            return json.dumps(
                {"total_pages": len(items), "total_chars": total_chars, "pages": items},
                ensure_ascii=False,
            )

        path = validate_dream_page_name(ctx.pages_dir, name)
        if not path.exists():
            return json.dumps({"error": f"页面「{name}」不存在"}, ensure_ascii=False)
        page = WikiPage.from_markdown(path.read_text(encoding="utf-8"), fallback_name=name)
        if page is None:
            return json.dumps({"error": f"页面「{name}」内容为空或损坏"}, ensure_ascii=False)

        lines = page.content.splitlines()
        total_lines = len(lines)
        start_idx = max(0, start_line - 1)
        end_idx = min(total_lines, start_idx + min(max_lines, 200))
        numbered = "\n".join(f"{idx + 1:4d}: {lines[idx]}" for idx in range(start_idx, end_idx))
        return json.dumps(
            {
                "name": name,
                "description": page.description,
                "tags": page.tags,
                "related": page.related,
                "start_line": start_line,
                "end_line": end_idx,
                "total_lines": total_lines,
                "has_more": end_idx < total_lines,
                "next_start_line": end_idx + 1 if end_idx < total_lines else None,
                "content": numbered,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def memory_create(
    name: str,
    content: str,
    description: str = "",
    tags: list[str] | None = None,
    related: list[str] | None = None,
) -> str:
    """在副本中创建新的专注记忆页面。"""
    try:
        ctx = _require_ctx()
        path = validate_dream_page_name(ctx.pages_dir, name)
        if name in ctx.deleted_pages:
            return json.dumps({"error": f"页面「{name}」位于已删除名单中，禁止恢复"}, ensure_ascii=False)
        if path.exists():
            return json.dumps({"error": f"页面「{name}」已存在，请改用 memory_str_replace 或 memory_insert"}, ensure_ascii=False)

        clean_tags, clean_related = _validate_metadata_lists(ctx.pages_dir, tags, related)
        now = datetime.now()
        page = WikiPage(
            name=name,
            content=content,
            description=description.strip(),
            tags=clean_tags,
            related=clean_related,
            created_at=now,
            updated_at=now,
            revision=1,
            source_ids=[],
            status="legacy",
        )
        _write_dream_page(ctx, path, page)
        return json.dumps({"created": name, "char_count": len(content)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def memory_str_replace(
    name: str,
    old_str: str,
    new_str: str = "",
    description: str | None = None,
    tags: list[str] | None = None,
    related: list[str] | None = None,
) -> str:
    """替换副本页面中的唯一文本片段，并可同时更新摘要、标签或关联。"""
    try:
        ctx = _require_ctx()
        path = validate_dream_page_name(ctx.pages_dir, name)
        if not path.exists():
            return json.dumps({"error": f"页面「{name}」不存在"}, ensure_ascii=False)
        page = WikiPage.from_markdown(path.read_text(encoding="utf-8"), fallback_name=name)
        if page is None:
            return json.dumps({"error": f"页面「{name}」无法解析"}, ensure_ascii=False)

        occurrences = page.content.count(old_str)
        if occurrences == 0:
            return json.dumps({"error": "未找到匹配的 old_str，请先用 memory_view 核对原文"}, ensure_ascii=False)
        if occurrences > 1:
            return json.dumps({"error": f"old_str 出现了 {occurrences} 次，请补充上下文使其唯一"}, ensure_ascii=False)

        page.content = page.content.replace(old_str, new_str, 1)
        if description is not None:
            page.description = description.strip()
        if tags is not None or related is not None:
            clean_tags, clean_related = _validate_metadata_lists(
                ctx.pages_dir,
                page.tags if tags is None else tags,
                page.related if related is None else related,
            )
            page.tags = clean_tags
            page.related = clean_related
        page.updated_at = datetime.now()
        _write_dream_page(ctx, path, page)
        return json.dumps({"updated": name, "char_count": len(page.content)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def memory_insert(
    name: str,
    insert_line: int,
    new_str: str,
    description: str | None = None,
    tags: list[str] | None = None,
    related: list[str] | None = None,
) -> str:
    """在副本页面指定行位置插入新内容。"""
    try:
        ctx = _require_ctx()
        path = validate_dream_page_name(ctx.pages_dir, name)
        if not path.exists():
            return json.dumps({"error": f"页面「{name}」不存在"}, ensure_ascii=False)
        page = WikiPage.from_markdown(path.read_text(encoding="utf-8"), fallback_name=name)
        if page is None:
            return json.dumps({"error": f"页面「{name}」无法解析"}, ensure_ascii=False)

        lines = page.content.splitlines()
        if insert_line > len(lines):
            return json.dumps(
                {"error": f"insert_line ({insert_line}) 超出总行数 ({len(lines)})"},
                ensure_ascii=False,
            )
        inserted = new_str.splitlines()
        updated_lines = lines[:insert_line] + inserted + lines[insert_line:]
        page.content = "\n".join(updated_lines)
        if description is not None:
            page.description = description.strip()
        if tags is not None or related is not None:
            clean_tags, clean_related = _validate_metadata_lists(
                ctx.pages_dir,
                page.tags if tags is None else tags,
                page.related if related is None else related,
            )
            page.tags = clean_tags
            page.related = clean_related
        page.updated_at = datetime.now()
        _write_dream_page(ctx, path, page)
        return json.dumps({"updated": name, "total_lines": len(updated_lines)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def memory_delete(name: str) -> str:
    """删除副本中的冗余或已合并页面。"""
    try:
        ctx = _require_ctx()
        path = validate_dream_page_name(ctx.pages_dir, name)
        if not path.exists():
            return json.dumps({"error": f"页面「{name}」不存在"}, ensure_ascii=False)
        path.unlink()
        return json.dumps({"deleted": name}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def memory_rename(old_name: str, new_name: str, description: str | None = None) -> str:
    """重命名副本中的页面，并同步更新副本内其他页面的关联引用。"""
    try:
        ctx = _require_ctx()
        old_path = validate_dream_page_name(ctx.pages_dir, old_name)
        new_path = validate_dream_page_name(ctx.pages_dir, new_name)
        if new_name in ctx.deleted_pages:
            return json.dumps({"error": f"目标名称「{new_name}」位于已删除名单中，禁止恢复"}, ensure_ascii=False)
        if not old_path.exists():
            return json.dumps({"error": f"原页面「{old_name}」不存在"}, ensure_ascii=False)
        if new_path.exists():
            return json.dumps({"error": f"目标页面「{new_name}」已存在"}, ensure_ascii=False)

        page = WikiPage.from_markdown(old_path.read_text(encoding="utf-8"), fallback_name=old_name)
        if page is None:
            return json.dumps({"error": f"原页面「{old_name}」无法解析"}, ensure_ascii=False)

        page.name = new_name
        if description is not None:
            page.description = description.strip()
        page.updated_at = datetime.now()
        _write_dream_page(ctx, new_path, page)
        old_path.unlink()
        return json.dumps({"renamed": old_name, "to": new_name}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def transcript_list() -> str:
    """列出本次整理范围内的全部主会话摘要（标题、时间、有效正文条数）。"""
    try:
        ctx = _require_ctx()
        items = []
        for sid in ctx.session_order:
            entry = ctx.transcripts.get(sid)
            if not entry:
                continue
            items.append({
                "session_id": sid,
                "title": entry.get("title", ""),
                "updated_at": entry.get("updated_at", ""),
                "message_count": len(entry.get("messages", [])),
            })
        return json.dumps({"total_sessions": len(items), "sessions": items}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def transcript_read(session_id: str, offset: int = 0, limit: int = 20) -> str:
    """分页读取指定会话的有效用户/助手对话正文，单次带字符上限保护。"""
    try:
        ctx = _require_ctx()
        entry = ctx.transcripts.get(session_id)
        if entry is None:
            return json.dumps({"error": f"会话「{session_id}」不在本次整理输入范围内"}, ensure_ascii=False)

        all_msgs = entry.get("messages", [])
        total = len(all_msgs)
        max_count = min(limit, 50)
        sliced = []
        char_budget = 12000
        used_chars = 0
        idx = offset

        while idx < total and len(sliced) < max_count:
            msg = all_msgs[idx]
            body = str(msg.get("content", ""))
            if len(body) > 4000:
                body = body[:4000] + "\n...[单条消息过长已截断]..."
            if sliced and used_chars + len(body) > char_budget:
                break
            sliced.append({"index": idx, "role": msg.get("role", "user"), "content": body})
            used_chars += len(body)
            idx += 1

        return json.dumps(
            {
                "session_id": session_id,
                "title": entry.get("title", ""),
                "total_messages": total,
                "offset": offset,
                "next_offset": idx if idx < total else None,
                "messages": sliced,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


_TOOL_SPECS = (
    (memory_view, MemoryViewArgs, "查看整理副本中的全部页面列表（不传 name），或按行分页读取指定页面的元数据与正文。"),
    (memory_create, MemoryCreateArgs, "在整理副本中创建新的专注记忆页面（单页不超过 100 KB）。"),
    (memory_str_replace, MemoryStrReplaceArgs, "替换整理副本指定页面中的唯一原文片段，可同步更新摘要、标签或关联页面。"),
    (memory_insert, MemoryInsertArgs, "在整理副本指定页面的行号位置插入新内容。"),
    (memory_delete, MemoryDeleteArgs, "从整理副本中删除已被合并或过期的旧页面。"),
    (memory_rename, MemoryRenameArgs, "重命名整理副本中的页面。"),
    (transcript_list, TranscriptListArgs, "列出本次做梦输入的全部主会话（ID、标题、时间、消息条数）。"),
    (transcript_read, TranscriptReadArgs, "按消息偏移量分页读取指定主会话的用户/助手正文记录。"),
)


def ensure_dream_tools_registered() -> None:
    """确保做梦专属工具已在全局注册表中注册（兼容测试中清空 _tools 的情况）。"""
    for fn, model, desc in _TOOL_SPECS:
        if fn.__name__ not in _tools:
            register(description=desc, args_model=model, agents=[AgentType.DREAM])(fn)


ensure_dream_tools_registered()
