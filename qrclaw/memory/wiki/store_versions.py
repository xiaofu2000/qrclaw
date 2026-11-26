"""整库归档版本管理（memory/.store-versions/<version_id>/）。

职责：
1. 做梦切换或整库回退前，将当前 pages/、index.json、index.md 完整归档到 .store-versions/<id>/；
2. 保留最近 store_versions_days 天内的全部版本，且至少保留最近 store_versions_keep 份；
3. 支持从任意归档版本整库回退（回退前同样归档当前库，保证版本号单调递增，重置对应 baselines 与失效 repairs）。
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import time
import uuid

from qrclaw.config import settings
from qrclaw.logger import get_logger
from qrclaw.memory.wiki.index import INDEX_JSON, INDEX_MD, atomic_write
from qrclaw.memory.wiki.page import WikiPage

logger = get_logger("qrclaw.memory.wiki.store_versions")

_VERSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,120}$")


class WikiStoreVersions:
    """管理记忆库的整库归档与回退。"""

    def __init__(self, memory):
        self.memory = memory
        self.root: Path = memory.memory_dir / ".store-versions"

    def _version_dir(self, version_id: str) -> Path:
        if not isinstance(version_id, str) or not _VERSION_ID_RE.match(version_id):
            raise ValueError(f"无效的整库版本 ID: {version_id!r}")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / version_id
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError(f"无效的整库版本 ID: {version_id!r}")
        return target

    def archive_current(self, *, reason: str = "", dream_id: str | None = None) -> dict:
        """归档当前 pages/、index.json 与 index.md；调用方应持有 Wiki 锁。"""
        with self.memory._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            version_id = f"sv_{stamp}_{uuid.uuid4().hex[:6]}"
            vdir = self._version_dir(version_id)
            vpages = vdir / "pages"
            vpages.mkdir(parents=True, exist_ok=True)

            pages_root = self.memory.pages_dir.resolve()
            page_count = 0
            total_chars = 0
            deleted = self.memory.provenance.read_readonly()["deleted_pages"]

            for md_file in sorted(self.memory.pages_dir.glob("*.md")):
                if not md_file.resolve().is_relative_to(pages_root):
                    continue
                if md_file.stem in deleted:
                    continue
                shutil.copy2(md_file, vpages / md_file.name)
                page_count += 1
                try:
                    parsed = WikiPage.from_markdown(md_file.read_text(encoding="utf-8"), fallback_name=md_file.stem)
                    if parsed:
                        total_chars += len(parsed.content)
                except Exception:
                    pass

            for idx_name in (INDEX_JSON, INDEX_MD):
                src = self.memory.memory_dir / idx_name
                if src.exists():
                    shutil.copy2(src, vdir / idx_name)

            meta = {
                "id": version_id,
                "created_at": time.time(),
                "reason": reason,
                "dream_id": dream_id,
                "page_count": page_count,
                "total_chars": total_chars,
            }
            atomic_write(vdir / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
            self.cleanup()
            return meta

    def cleanup(self, *, keep: int | None = None, days: int | None = None) -> int:
        """保留最近 days 天内的版本，且至少保留最近 keep 份。"""
        if not self.root.exists():
            return 0
        keep_count = settings.memory.store_versions_keep if keep is None else max(1, keep)
        keep_days = settings.memory.store_versions_days if days is None else max(1, days)
        cutoff = time.time() - keep_days * 86400

        versions = self.list_versions()
        removed = 0
        for idx, meta in enumerate(versions):
            if idx < keep_count:
                continue
            if float(meta.get("created_at") or 0) < cutoff:
                try:
                    vdir = self._version_dir(meta["id"])
                    shutil.rmtree(vdir, ignore_errors=True)
                    removed += 1
                except Exception:
                    continue
        return removed

    def list_versions(self) -> list[dict]:
        """按创建时间倒序返回所有整库归档版本摘要。"""
        if not self.root.exists():
            return []
        root_resolved = self.root.resolve()
        items: list[dict] = []
        for vdir in self.root.iterdir():
            if not vdir.is_dir() or not vdir.resolve().is_relative_to(root_resolved):
                continue
            meta_file = vdir / "meta.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    if isinstance(meta, dict) and meta.get("id"):
                        items.append(meta)
                        continue
                except Exception:
                    pass
            # 兼容缺少 meta.json 的目录
            vpages = vdir / "pages"
            md_files = list(vpages.glob("*.md")) if vpages.exists() else []
            items.append({
                "id": vdir.name,
                "created_at": vdir.stat().st_mtime,
                "reason": "",
                "dream_id": None,
                "page_count": len(md_files),
                "total_chars": 0,
            })
        items.sort(key=lambda item: (float(item.get("created_at") or 0), item.get("id", "")), reverse=True)
        return items

    def restore(self, version_id: str) -> dict:
        """恢复到指定整库版本；恢复前自动归档当前库，并保持版本号单调递增。"""
        vdir = self._version_dir(version_id)
        vpages = vdir / "pages"
        if not vdir.exists() or not vpages.exists():
            raise FileNotFoundError("整库版本不存在")

        with self.memory._lock:
            # 1. 先归档当前记忆库
            archived_prev = self.archive_current(reason=f"整库回退至 {version_id} 前自动归档")

            state = self.memory.provenance.read()
            pages_root = self.memory.pages_dir.resolve()

            # 2. 记录当前所有页面的版本号下限，确保恢复后版本号只增不减
            current_revs: dict[str, int] = {}
            for md_file in self.memory.pages_dir.glob("*.md"):
                if not md_file.resolve().is_relative_to(pages_root):
                    continue
                cur = self.memory.get_page(md_file.stem)
                if cur is not None:
                    current_revs[cur.name] = cur.revision
                    state["page_revisions"][cur.name] = max(
                        cur.revision, state["page_revisions"].get(cur.name, 0)
                    )

            # 3. 读取目标归档版本中的所有页面并重新分配递增版本号
            vpages_root = vpages.resolve()
            restored_pages: dict[str, WikiPage] = {}
            for md_file in sorted(vpages.glob("*.md")):
                if not md_file.resolve().is_relative_to(vpages_root):
                    continue
                self.memory._page_path(md_file.stem)
                page = WikiPage.from_markdown(md_file.read_text(encoding="utf-8"), fallback_name=md_file.stem)
                if page is None:
                    continue
                page.name = md_file.stem
                floor = max(
                    page.revision,
                    current_revs.get(page.name, 0),
                    state["page_revisions"].get(page.name, 0),
                )
                page.revision = floor + 1
                state["page_revisions"][page.name] = page.revision

                # 校验来源是否仍然有效；若归档后有聊天消息被删除，则转入待修复状态，不暴露已撤销事实
                if page.source_ids:
                    all_valid = all(
                        state["sources"].get(sid, {}).get("state") == "valid"
                        for sid in page.source_ids
                    )
                    if not all_valid:
                        page.status = "pending"
                        self.memory.provenance._queue(state, page.name, "整库回退后部分来源已撤销")
                    else:
                        page.status = "ready"
                else:
                    page.status = "legacy"

                restored_pages[page.name] = page

            # 4. 原子替换 pages/ 目录下的文件（移除不在目标版本中的页面，不写删除标记）
            for name, page in restored_pages.items():
                target_path = self.memory._page_path(name)
                atomic_write(target_path, page.to_markdown())
                state["deleted_pages"].pop(name, None)
                state["baselines"].pop(name, None)
                if page.status in ("ready", "legacy"):
                    state["repairs"].pop(name, None)

            for md_file in list(self.memory.pages_dir.glob("*.md")):
                if not md_file.resolve().is_relative_to(pages_root):
                    continue
                if md_file.stem not in restored_pages:
                    md_file.unlink(missing_ok=True)
                    state["baselines"].pop(md_file.stem, None)
                    state["repairs"].pop(md_file.stem, None)

            # 清理指向不存在页面的残留修复队列
            for rname in list(state["repairs"]):
                if rname not in restored_pages:
                    state["repairs"].pop(rname, None)

            self.memory.provenance.write(state)
            self.memory.rebuild_index()
            self.memory.index.append_log(
                "store_restore",
                version_id,
                f"恢复整库至 {version_id}（共 {len(restored_pages)} 页），原库已归档为 {archived_prev['id']}",
            )
            self.memory.invalidate_cache()

        if any(p.status == "pending" for p in restored_pages.values()):
            self.memory.repair_worker.resume()

        return {
            "restored_version": version_id,
            "archived_version": archived_prev["id"],
            "page_count": len(restored_pages),
        }
