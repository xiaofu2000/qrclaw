"""
IndexManager —— 维护 index.json 和 index.md 的双向同步

职责：
- index.json：结构化索引，代码层快速查找页面元数据
- index.md：人类可读目录，注入 LLM system prompt
- log.md：append-only 操作日志

两者保持同步：每次 upsert/remove 操作后自动重建 index.md。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from qrclaw.logger import get_logger
from qrclaw.memory.wiki.page import WikiPage

logger = get_logger("qrclaw.memory.wiki.index")

INDEX_JSON = "index.json"
INDEX_MD = "index.md"
LOG_MD = "log.md"


class IndexManager:
    """
    维护 index.json、index.md、log.md 三个文件的同步。

    index.json 是 source of truth，index.md 由它生成。
    """

    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self._index_json_path = memory_dir / INDEX_JSON
        self._index_md_path = memory_dir / INDEX_MD
        self._log_path = memory_dir / LOG_MD
        self._ensure_files()

    # ── 初始化 ────────────────────────────────────────────────────────────────

    def _ensure_files(self) -> None:
        """确保三个文件存在"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "pages").mkdir(exist_ok=True)

        if not self._index_json_path.exists():
            self._write_json({"version": "2.0", "updated_at": _now(), "pages": []})

        if not self._index_md_path.exists():
            self._index_md_path.write_text(
                "# QRClaw Wiki 索引\n\n> 暂无页面\n", encoding="utf-8"
            )

        if not self._log_path.exists():
            self._log_path.write_text("# QRClaw Wiki 操作日志\n\n", encoding="utf-8")

    # ── 读取 ──────────────────────────────────────────────────────────────────

    def _read_json(self) -> dict:
        """读取 index.json"""
        try:
            return json.loads(self._index_json_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"读取 index.json 失败: {e}")
            return {"version": "2.0", "updated_at": _now(), "pages": []}

    def _write_json(self, data: dict) -> None:
        """写入 index.json"""
        data["updated_at"] = _now()
        self._index_json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── 页面索引操作 ──────────────────────────────────────────────────────────

    def upsert(self, page: WikiPage) -> None:
        """更新或插入页面索引条目，然后重建 index.md"""
        data = self._read_json()
        pages = data.get("pages", [])

        # 查找是否已存在
        idx = next((i for i, p in enumerate(pages) if p["name"] == page.name), -1)
        entry = page.to_index_entry()

        if idx >= 0:
            pages[idx] = entry
        else:
            pages.append(entry)

        data["pages"] = pages
        self._write_json(data)
        self._rebuild_index_md(pages)
        logger.debug(f"IndexManager.upsert: {page.name}")

    def remove(self, name: str) -> None:
        """删除页面索引条目"""
        data = self._read_json()
        pages = [p for p in data.get("pages", []) if p["name"] != name]
        data["pages"] = pages
        self._write_json(data)
        self._rebuild_index_md(pages)
        logger.debug(f"IndexManager.remove: {name}")

    def get_entry(self, name: str) -> Optional[dict]:
        """从 index.json 快速查找页面元数据（不读磁盘页面文件）"""
        data = self._read_json()
        return next((p for p in data.get("pages", []) if p["name"] == name), None)

    def all_entries(self) -> list[dict]:
        """返回所有页面元数据列表"""
        return self._read_json().get("pages", [])

    def exists(self, name: str) -> bool:
        """检查页面是否存在于索引中"""
        return self.get_entry(name) is not None

    # ── 重建 index.md ─────────────────────────────────────────────────────────

    def _rebuild_index_md(self, pages: list[dict]) -> None:
        """从 pages 列表重建 index.md"""
        if not pages:
            self._index_md_path.write_text(
                "# QRClaw Wiki 索引\n\n> 暂无页面\n", encoding="utf-8"
            )
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "# QRClaw Wiki 索引",
            "",
            f"> 最后更新: {now} | 共 {len(pages)} 个页面",
            "",
        ]

        # 按 tags 分组
        groups: dict[str, list[dict]] = {}
        untagged: list[dict] = []

        for p in pages:
            tags = p.get("tags", [])
            if tags:
                primary = tags[0]
                groups.setdefault(primary, []).append(p)
            else:
                untagged.append(p)

        for group_name, group_pages in groups.items():
            lines.append(f"## {group_name}")
            lines.append("")
            for p in group_pages:
                desc = p.get("description", "")
                lines.append(f"- [{p['name']}]({p['file']}) — {desc}")
            lines.append("")

        if untagged:
            lines.append("## 未分类")
            lines.append("")
            for p in untagged:
                desc = p.get("description", "")
                lines.append(f"- [{p['name']}]({p['file']}) — {desc}")
            lines.append("")

        self._index_md_path.write_text("\n".join(lines), encoding="utf-8")

    def rebuild_from_pages_dir(self) -> None:
        """从 pages/ 目录完整重建 index.json 和 index.md（用于恢复/修复）"""
        pages_dir = self.memory_dir / "pages"
        pages = []

        for md_file in sorted(pages_dir.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            page = WikiPage.from_markdown(text, fallback_name=md_file.stem)
            if page:
                pages.append(page.to_index_entry())

        data = {"version": "2.0", "updated_at": _now(), "pages": pages}
        self._write_json(data)
        self._rebuild_index_md(pages)
        logger.info(f"从 pages/ 重建索引，共 {len(pages)} 个页面")

    # ── index.md 读取（注入 system prompt 用）────────────────────────────────

    def load_index_md(self) -> str:
        """返回 index.md 全文"""
        if self._index_md_path.exists():
            return self._index_md_path.read_text(encoding="utf-8")
        return "# QRClaw Wiki 索引\n\n> 暂无页面\n"

    # ── 日志 ──────────────────────────────────────────────────────────────────

    def append_log(self, action: str, page_name: str, detail: str = "") -> None:
        """
        追加一条日志到 log.md

        格式：## [2026-04-10 12:00:00] write | qrclaw架构
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n## [{timestamp}] {action} | {page_name}\n"
        if detail:
            entry += f"{detail}\n"

        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(entry)


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat()
