"""单页不可变历史版本管理（memory/.history/<页面名>/r<版本号>.md）。

职责：
1. 页面被覆盖或删除前，将旧页面保存到 .history/<页面名>/r<revision>.md；
2. 每页保留最近 history_days 天内的全部版本，且至少保留最近 history_keep 个版本；
3. 来源被撤销（invalidate）时同步删除 source_ids 与被撤销来源有交集的历史快照；
4. 单页回滚按「用户整页确认」处理，复用 provenance.manual_edit() 生成 manual 来源，版本号单调递增。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import time
import uuid

from qrclaw.config import settings
from qrclaw.logger import get_logger
from qrclaw.memory.wiki.index import atomic_write
from qrclaw.memory.wiki.page import WikiPage

logger = get_logger("qrclaw.memory.wiki.history")

_REV_FILE_RE = re.compile(r"^r(\d+)\.md$")


class WikiPageHistory:
    """管理指定 WikiMemory 工作空间下的单页历史快照。"""

    def __init__(self, memory):
        self.memory = memory
        self.root: Path = memory.memory_dir / ".history"

    def _page_dir(self, name: str) -> Path:
        """复用 WikiMemory._page_path 校验页面名，防止路径穿越。"""
        self.memory._page_path(name)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError(f"无效的页面名称: {name!r}")
        return target

    def save_snapshot(self, page: WikiPage) -> Path | None:
        """在页面被覆盖或删除前保存快照；含失效来源或处于异常整理态的旧页不入历史。"""
        if page is None or page.revision <= 0:
            return None
        if page.status not in ("ready", "legacy"):
            return None
        if page.source_ids and not self.memory.provenance.current(page.source_ids):
            return None

        page_dir = self._page_dir(page.name)
        page_dir.mkdir(parents=True, exist_ok=True)
        rev_path = page_dir / f"r{page.revision}.md"
        atomic_write(rev_path, page.to_markdown())
        self.cleanup_page(page.name)
        return rev_path

    def cleanup_page(
        self,
        name: str,
        *,
        keep: int | None = None,
        days: int | None = None,
    ) -> int:
        """清理超期且超出最低保留份数的单页历史版本。"""
        page_dir = self._page_dir(name)
        if not page_dir.exists():
            return 0

        keep_count = settings.memory.history_keep if keep is None else max(1, keep)
        keep_days = settings.memory.history_days if days is None else max(1, days)
        cutoff = time.time() - keep_days * 86400

        entries: list[tuple[int, Path]] = []
        for path in page_dir.glob("r*.md"):
            match = _REV_FILE_RE.match(path.name)
            if match:
                entries.append((int(match.group(1)), path))

        entries.sort(key=lambda item: item[0], reverse=True)
        removed = 0
        for idx, (_rev, path) in enumerate(entries):
            if idx < keep_count:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed

    def purge_sources(self, invalid_source_ids: set[str]) -> int:
        """撤销聊天来源时，删除引用了被撤销来源的所有单页历史版本。"""
        if not invalid_source_ids or not self.root.exists():
            return 0

        removed = 0
        root_resolved = self.root.resolve()
        for page_dir in list(self.root.iterdir()):
            if not page_dir.is_dir() or not page_dir.resolve().is_relative_to(root_resolved):
                continue
            for rev_file in list(page_dir.glob("r*.md")):
                if not rev_file.resolve().is_relative_to(root_resolved):
                    continue
                try:
                    page = WikiPage.from_markdown(
                        rev_file.read_text(encoding="utf-8"),
                        fallback_name=page_dir.name,
                    )
                except Exception:
                    continue
                if page and invalid_source_ids.intersection(page.source_ids):
                    rev_file.unlink(missing_ok=True)
                    removed += 1
            try:
                if not any(page_dir.iterdir()):
                    page_dir.rmdir()
            except OSError:
                pass
        return removed

    def list_versions(self, name: str) -> list[dict]:
        """按版本号倒序返回指定页面的历史版本列表。"""
        page_dir = self._page_dir(name)
        if not page_dir.exists():
            return []

        items: list[tuple[int, dict]] = []
        for rev_file in page_dir.glob("r*.md"):
            match = _REV_FILE_RE.match(rev_file.name)
            if not match:
                continue
            rev = int(match.group(1))
            try:
                page = WikiPage.from_markdown(rev_file.read_text(encoding="utf-8"), fallback_name=name)
            except Exception as exc:
                logger.warning("读取历史版本失败 %s r%s: %s", name, rev, exc)
                continue
            if page is None:
                continue
            items.append((
                rev,
                {
                    "name": name,
                    "revision": page.revision or rev,
                    "description": page.description,
                    "tags": page.tags,
                    "related": page.related,
                    "status": page.status,
                    "source_count": len(page.source_ids),
                    "char_count": len(page.content),
                    "created_at": page.created_at.isoformat(),
                    "updated_at": page.updated_at.isoformat(),
                },
            ))
        items.sort(key=lambda item: item[0], reverse=True)
        return [entry for _rev, entry in items]

    def get_version(self, name: str, revision: int) -> dict:
        """读取指定页面的某一历史版本正文与元数据。"""
        page = self._read_snapshot(name, revision)
        return {
            "name": name,
            "revision": page.revision or revision,
            "content": page.content,
            "description": page.description,
            "tags": page.tags,
            "related": page.related,
            "status": page.status,
            "source_count": len(page.source_ids),
            "char_count": len(page.content),
            "created_at": page.created_at.isoformat(),
            "updated_at": page.updated_at.isoformat(),
        }

    def _read_snapshot(self, name: str, revision: int) -> WikiPage:
        if type(revision) is not int or revision < 1:
            raise ValueError("历史版本号必须为正整数")
        page_dir = self._page_dir(name)
        rev_file = page_dir / f"r{revision}.md"
        if not rev_file.exists():
            raise FileNotFoundError("历史版本不存在")
        page = WikiPage.from_markdown(rev_file.read_text(encoding="utf-8"), fallback_name=name)
        if page is None:
            raise FileNotFoundError("历史版本不存在")
        page.revision = revision
        return page

    def restore(self, name: str, revision: int, current_revision: int | None = None) -> WikiPage:
        """将页面回滚到指定历史版本；复用 manual_edit 生成 manual 来源并保持版本号单调递增。"""
        with self.memory._lock:
            snap = self._read_snapshot(name, revision)
            state = self.memory.provenance.read()
            current = None if name in state["deleted_pages"] else self.memory.get_page(name)

            if current is not None:
                expected_rev = current.revision if current_revision is None else current_revision
                if current.revision != expected_rev:
                    raise RuntimeError("记忆已被其他任务修改，请重新载入")
                restored = self.memory.provenance.manual_edit(
                    name,
                    current.revision,
                    snap.content,
                    snap.description,
                    list(snap.tags),
                    list(snap.related),
                )
                self.memory.index.append_log(
                    "单页回滚",
                    name,
                    f"从历史版本 r{revision} 回滚为版本 {restored.revision}",
                )
                return restored

            # 已删除页面从历史版本恢复：同样建立 manual 确认来源并清除删除标记
            if current_revision not in (None, 0):
                raise RuntimeError("记忆已被删除，请刷新后重试恢复")
            if not snap.content.strip():
                raise ValueError("历史版本正文为空，无法恢复")
            source_id = uuid.uuid4().hex
            state["source_order"] = state.get("source_order", 0) + 1
            state["sources"][source_id] = {
                "id": source_id,
                "session_id": None,
                "message_id": None,
                "version": hashlib.sha256(snap.content.encode()).hexdigest(),
                "role": "user",
                "kind": "manual",
                "page_name": name,
                "state": "valid",
                "captured_at": time.time(),
                "before_hash": "",
                "order": state["source_order"],
                "diff": f"从历史版本 r{revision} 恢复已删除页面",
                "body": "用户确认完整替换此页；未出现在以下正文的旧事实已撤销，禁止从旧来源补回。\n" + snap.content,
                "description": snap.description,
                "tags": list(snap.tags),
                "related": list(snap.related),
            }
            self.memory.provenance.write(state)
            restored = self.memory.save_page(
                name,
                snap.content,
                snap.description,
                list(snap.tags),
                list(snap.related),
                source_ids=[source_id],
                status="ready",
                explicit=True,
                started_at=time.time(),
            )
            latest = self.memory.provenance.read()
            latest["baselines"].pop(name, None)
            latest["repairs"].pop(name, None)
            self.memory.provenance.write(latest)
            self.memory.index.append_log(
                "单页回滚",
                name,
                f"从历史版本 r{revision} 恢复页面为版本 {restored.revision}",
            )
            self.memory.invalidate_cache()
            return restored
