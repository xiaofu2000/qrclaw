"""工作空间级记忆来源、撤销标记和可恢复的页面整理队列。"""
from __future__ import annotations

from copy import deepcopy
from difflib import unified_diff
import hashlib
import json
from pathlib import Path
import time
import uuid

from qrclaw.memory.wiki.index import atomic_write
from qrclaw.memory.wiki.extraction.strategies import filter_memory_messages
from qrclaw.memory.wiki.page import wiki_links

_COMPACT_AUTO_THRESHOLD = 1500


def source_text(sources: list[dict]) -> str:
    """携带真实来源 ID 和角色；正文是证据，不能作为维护指令执行。"""
    parts = []
    for source in sources:
        text = f"[来源 {source['id']}；{source['role']}；{source.get('kind', 'chat')}]\n{source['body']}"
        if source.get("kind") == "manual":
            metadata = {key: source.get(key) for key in ("description", "tags", "related")}
            text += "\n用户确认的页面元数据：" + json.dumps(metadata, ensure_ascii=False)
        parts.append(text)
    return "\n\n".join(parts)


def _empty_state() -> dict:
    return {
        "sources": {},
        "deleted_pages": {},
        "repairs": {},
        "baselines": {},
        "page_revisions": {},
        "session_changes": {},
        "last_activity": 0,
    }


class WikiProvenance:
    """复用 Wiki 文件锁，原子维护来源及队列，避免撤销与保存交错。"""

    def __init__(self, memory):
        """每个工作空间使用独立状态文件，并按文件签名缓存解析快照。"""
        self.memory = memory
        self.path: Path = memory.memory_dir / ".wiki-state.json"
        self._cache_sig: tuple[int, int, int, int] | None = None
        self._cache_state: dict | None = None

    def _file_sig(self) -> tuple[int, int, int, int] | None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)

    def read_readonly(self) -> dict:
        """只读访问缓存的来源状态快照（调用方绝不能原地修改返回值）。"""
        sig = self._file_sig()
        if sig is None:
            self._cache_sig = None
            self._cache_state = None
            return _empty_state()
        if self._cache_state is not None and self._cache_sig == sig:
            return self._cache_state

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data.setdefault("page_revisions", {})
            data.setdefault("session_changes", {})
            for key in ("sources", "deleted_pages", "repairs", "baselines", "page_revisions", "session_changes"):
                if not isinstance(data[key], dict):
                    raise ValueError(key)
            # 单调序号避免同一秒的聊天与手动修改无法确定先后；旧记录按原时间迁移。
            needs_migration = any("order" not in s for s in data["sources"].values())
            if needs_migration:
                for order, source in enumerate(sorted(data["sources"].values(), key=lambda s: s["captured_at"]), 1):
                    source.setdefault("order", order)
            data["source_order"] = max(
                data.get("source_order", 0),
                max((s["order"] for s in data["sources"].values()), default=0),
            )
            self._cache_sig = sig
            self._cache_state = data
            return data
        except (ValueError, KeyError, TypeError) as exc:
            self._cache_sig = None
            self._cache_state = None
            raise ValueError("Wiki 来源进度损坏，请检查 .wiki-state.json") from exc

    def read(self) -> dict:
        """调用方持有 Wiki 锁；返回安全深拷贝供修改后写回，损坏状态明确报错。"""
        return deepcopy(self.read_readonly())

    def write(self, state: dict) -> None:
        """调用方持有 Wiki 锁；单文件原子替换并同步更新内存缓存签名。"""
        atomic_write(self.path, json.dumps(state, ensure_ascii=False, indent=2))
        sig = self._file_sig()
        if sig is not None:
            self._cache_sig = sig
            self._cache_state = deepcopy(state)
        else:
            self._cache_sig = None
            self._cache_state = None

    def begin_request(self) -> tuple[float, int]:
        """请求入队时预留顺序，避免延迟执行的旧请求被当成新的用户更正。"""
        with self.memory._lock:
            state = self.read()
            state["source_order"] = state.get("source_order", 0) + 1
            self.write(state)
            return time.time(), state["source_order"]

    def capture(self, messages: list, session_id: str, *, allow_renew: bool = False, request_order: int | None = None) -> list[dict]:
        """为有效正文建立稳定版本；旧版本撤销后不允许旧工作线程复活它。"""
        captured = []
        with self.memory._lock:
            state = self.read()
            if session_id in state["session_changes"]:
                return []
            current = filter_memory_messages(messages)
            last_user_index = next((i for i in range(len(current) - 1, -1, -1) if current[i]["role"] == "user"), len(current))
            for index, message in enumerate(current):
                version = hashlib.sha256(f"{message['role']}\0{message['content']}".encode()).hexdigest()
                source_id = hashlib.sha256(f"{session_id}\0{message['uuid']}\0{version}".encode()).hexdigest()
                existing = state["sources"].get(source_id)
                if existing and existing["state"] != "valid":
                    existing = next((s for s in state["sources"].values() if s.get("session_id") == session_id
                        and s.get("message_id") == message["uuid"] and s.get("version") == version and s["state"] == "valid"), None)
                    if existing is None:
                        if not allow_renew:
                            continue
                        source_id = uuid.uuid4().hex
                if existing is None:
                    # 本轮助手背景也属于同一个旧请求，不能因完成较晚而越过用户确认。
                    if request_order is not None and index >= last_user_index:
                        order = request_order
                    else:
                        state["source_order"] = state.get("source_order", 0) + 1
                        order = state["source_order"]
                    existing = {"id": source_id, "session_id": session_id, "message_id": message["uuid"],
                                "version": version, "role": message["role"], "kind": "chat", "state": "valid",
                                "captured_at": time.time(), "order": order, "body": message["content"]}
                    state["sources"][source_id] = existing
                captured.append(deepcopy(existing))
            if len(state["sources"]) > _COMPACT_AUTO_THRESHOLD:
                self._compact_state(state, protect_ids={s["id"] for s in captured})
            self.write(state)
        return captured

    def valid_session_sources(self, session_id: str) -> list[dict]:
        """按顺序读取指定会话的全部有效聊天来源原文，供做梦拼接被压缩前的完整对话。"""
        with self.memory._lock:
            state = self.read_readonly()
            if session_id in state["session_changes"]:
                return []
            items = [
                deepcopy(s)
                for s in state["sources"].values()
                if s.get("session_id") == session_id
                and s.get("kind", "chat") == "chat"
                and s.get("state") == "valid"
                and isinstance(s.get("body"), str)
                and s["body"].strip()
            ]
        items.sort(key=lambda s: (s.get("order", 0), s.get("captured_at", 0)))
        return items

    def invalidate(self, session_id: str, message_ids: set[str] | None = None, *, session_change: dict | None = None) -> None:
        """清除来源正文并排队受影响页面；同步删除引用该来源的单页历史版本。"""
        with self.memory._lock:
            state = self.read()
            if session_change is not None:
                state["session_changes"][session_id] = session_change
            invalid = set()
            for source in state["sources"].values():
                if source.get("session_id") == session_id and (message_ids is None or source.get("message_id") in message_ids):
                    source["state"] = "withdrawn"
                    source.pop("body", None)
                    invalid.add(source["id"])
            state["last_activity"] = time.time()
            pages = self.memory.list_pages(include_unavailable=True)
            for page in pages:
                if invalid.intersection(page.source_ids):
                    self._queue(state, page.name, "聊天来源已编辑或删除")
            # 队列先落盘，读取路径也检查来源状态，崩溃不会暴露失效正文。
            self.write(state)
            if invalid:
                self.memory.history.purge_sources(invalid)
            for page in pages:
                if invalid.intersection(page.source_ids):
                    self.memory.save_page(page.name, page.content, page.description, page.tags, page.related,
                                          source_ids=page.source_ids, status="pending", allow_invalid_sources=True)
        self.memory.invalidate_cache()
        self.memory.repair_worker.resume()

    def _referenced_source_ids(self, state: dict) -> set[str]:
        """收集当前所有页面（含待修复页）和单页历史版本正在引用的来源 ID。"""
        referenced: set[str] = set()
        pages_root = self.memory.pages_dir.resolve()
        for md_file in self.memory.pages_dir.glob("*.md"):
            if not md_file.resolve().is_relative_to(pages_root):
                continue
            page = self.memory.get_page(md_file.stem)
            if page is not None:
                referenced.update(page.source_ids)
        for job in state.get("repairs", {}).values():
            for rec in job.get("records", []):
                referenced.update(rec.get("source_ids") or [])
        return referenced

    def _compact_state(
        self,
        state: dict,
        *,
        sessions_dir: Path | None = None,
        max_withdrawn: int = 500,
        max_unreferenced_chat: int = 1000,
        keep_days: int = 30,
        keep_sessions: int = 50,
        protect_ids: set[str] | None = None,
    ) -> int:
        """原地压缩清理未引用且过期/孤立的来源条目，绝不删除现有页面引用的有效来源。"""
        sources: dict[str, dict] = state["sources"]
        if not sources:
            return 0

        referenced = self._referenced_source_ids(state)
        if protect_ids:
            referenced.update(protect_ids)

        now = time.time()
        cutoff = now - keep_days * 86400
        existing_sessions: set[str] | None = None
        if sessions_dir is not None and sessions_dir.exists():
            existing_sessions = {p.stem for p in sessions_dir.glob("*.json")}

        removed_ids: set[str] = set()

        # 1. 清理未被任何页面引用的 withdrawn 记录（超 30 天，或总量超过 max_withdrawn）
        unreferenced_withdrawn: list[tuple[float, int, str]] = []
        for sid, src in sources.items():
            if sid in referenced or src.get("state") != "withdrawn":
                continue
            captured_at = float(src.get("captured_at") or 0)
            if captured_at < cutoff:
                removed_ids.add(sid)
            else:
                unreferenced_withdrawn.append((captured_at, int(src.get("order") or 0), sid))

        if len(unreferenced_withdrawn) > max_withdrawn:
            unreferenced_withdrawn.sort()
            for _, _, sid in unreferenced_withdrawn[: len(unreferenced_withdrawn) - max_withdrawn]:
                removed_ids.add(sid)

        # 2. 清理未被任何页面引用的旧 chat 有效来源（已删除会话、超 30 天、超出最近 50 个会话或超出条数上限）
        pending_changes = state.get("session_changes", {})
        unreferenced_valid_chat: list[dict] = []
        for sid, src in sources.items():
            if sid in referenced or sid in removed_ids:
                continue
            if src.get("state") != "valid":
                continue
            kind = src.get("kind", "chat")
            captured_at = float(src.get("captured_at") or 0)
            if kind == "manual":
                if captured_at < cutoff:
                    removed_ids.add(sid)
                continue

            session_id = src.get("session_id")
            if (
                existing_sessions is not None
                and session_id
                and session_id not in existing_sessions
                and session_id not in pending_changes
            ):
                removed_ids.add(sid)
                continue

            if captured_at < cutoff:
                removed_ids.add(sid)
                continue

            unreferenced_valid_chat.append(src)

        if unreferenced_valid_chat:
            session_latest: dict[str, float] = {}
            for src in unreferenced_valid_chat:
                sess = str(src.get("session_id") or "")
                ts = float(src.get("captured_at") or 0)
                if ts > session_latest.get(sess, -1.0):
                    session_latest[sess] = ts

            if len(session_latest) > keep_sessions:
                ranked_sessions = sorted(session_latest.items(), key=lambda item: item[1], reverse=True)
                allowed_sessions = {sess for sess, _ in ranked_sessions[:keep_sessions]}
                still_kept: list[dict] = []
                for src in unreferenced_valid_chat:
                    if str(src.get("session_id") or "") not in allowed_sessions:
                        removed_ids.add(src["id"])
                    else:
                        still_kept.append(src)
                unreferenced_valid_chat = still_kept

            if len(unreferenced_valid_chat) > max_unreferenced_chat:
                unreferenced_valid_chat.sort(key=lambda s: (int(s.get("order") or 0), float(s.get("captured_at") or 0)))
                for src in unreferenced_valid_chat[: len(unreferenced_valid_chat) - max_unreferenced_chat]:
                    removed_ids.add(src["id"])

        for sid in removed_ids:
            sources.pop(sid, None)
        return len(removed_ids)

    def compact(
        self,
        sessions_dir: Path | None = None,
        *,
        max_withdrawn: int = 500,
        max_unreferenced_chat: int = 1000,
        keep_days: int = 30,
        keep_sessions: int = 50,
    ) -> int:
        """压缩 .wiki-state.json 中未引用且过期或会话已删除的来源记录。"""
        with self.memory._lock:
            if not self.path.exists():
                return 0
            state = self.read()
            removed = self._compact_state(
                state,
                sessions_dir=sessions_dir,
                max_withdrawn=max_withdrawn,
                max_unreferenced_chat=max_unreferenced_chat,
                keep_days=keep_days,
                keep_sessions=keep_sessions,
            )
            if removed > 0:
                self.write(state)
            return removed

    def _queue(self, state: dict, name: str, reason: str) -> None:
        """新的撤销生成新任务标识，使正在执行的旧整理无法确认新任务。"""
        previous = state["repairs"].get(name, {})
        state["repairs"][name] = {"id": uuid.uuid4().hex, "name": name, "status": "pending", "reason": reason,
                                  "queued_at": time.time(), "last_attempt": previous.get("last_attempt", 0),
                                  "error": "", "records": previous.get("records", [])}

    def queue(self, name: str) -> None:
        """手动重试只排队，仍遵守闲置和冷却。"""
        with self.memory._lock:
            state = self.read()
            page = self.memory.get_page(name)
            if not page or name in state["deleted_pages"]:
                raise FileNotFoundError("记忆页面不存在")
            self._queue(state, name, "用户请求重新整理")
            state["last_activity"] = time.time()
            self.write(state)
            self.memory.save_page(name, page.content, page.description, page.tags, page.related,
                                  source_ids=page.source_ids, status="pending", allow_invalid_sources=True)
        self.memory.invalidate_cache()
        self.memory.repair_worker.resume()

    def current(self, source_ids: list[str]) -> bool:
        """调用方持有 Wiki 锁；写入前核实所有来源仍然有效。"""
        sources = self.read_readonly()["sources"]
        return all(sources.get(key, {}).get("state") == "valid" for key in source_ids)

    def baseline(self, page) -> None:
        """仅首次接入来源前保存独立旧基线，禁止用后来的混合正文补造证据。"""
        with self.memory._lock:
            if page and not page.source_ids:
                state = self.read()
                if page.name not in state["baselines"]:
                    state["baselines"][page.name] = {"content": page.content, "description": page.description,
                                                    "tags": page.tags, "related": page.related}
                    self.write(state)

    def manual_edit(self, name: str, revision: int, content: str, description: str, tags: list[str], related: list[str]):
        """用户整页确认取代旧事实来源；后续撤销聊天不会撤销独立的用户确认。"""
        if not content.strip():
            raise ValueError("记忆正文不能为空")
        with self.memory._lock:
            page = self.memory.get_page(name)
            if page is None:
                raise FileNotFoundError("记忆页面不存在")
            if page.revision != revision:
                raise RuntimeError("记忆已被其他任务修改，请重新载入；你的草稿已保留")
            state = self.read()
            source_id = uuid.uuid4().hex
            state["source_order"] = state.get("source_order", 0) + 1
            before = f"摘要：{page.description}\n标签：{', '.join(page.tags)}\n关联：{', '.join(page.related)}\n\n{page.content}"
            after = f"摘要：{description}\n标签：{', '.join(tags)}\n关联：{', '.join(related)}\n\n{content}"
            state["sources"][source_id] = {"id": source_id, "session_id": None, "message_id": None,
                "version": hashlib.sha256(content.encode()).hexdigest(), "role": "user", "kind": "manual",
                "page_name": name, "state": "valid", "captured_at": time.time(), "before_hash": hashlib.sha256(page.content.encode()).hexdigest(),
                "order": state["source_order"],
                "diff": "\n".join(unified_diff(before.splitlines(), after.splitlines(), fromfile="修改前", tofile="用户确认", lineterm="")),
                "body": "用户确认完整替换此页；未出现在以下正文的旧事实已撤销，禁止从旧来源补回。\n" + content,
                "description": description, "tags": tags, "related": related}
            self.write(state)
            saved = self.memory.save_page(name, content, description, tags, related, expected_page=page,
                                          source_ids=[source_id], status="ready")
            # 页面保存成功后才撤销旧维护任务；失败仍可用原基线继续整理。
            state["baselines"].pop(name, None)
            state["repairs"].pop(name, None)
            self.write(state)
            self.memory.index.append_log("用户编辑", name, f"版本 {page.revision} → {saved.revision}；来源 {source_id}")
        self.memory.invalidate_cache()
        return saved

    def detail(self, page) -> dict:
        """Web 可查看待整理页面和来源；撤销来源只返回标记，不返回已删除的正文。"""
        with self.memory._lock:
            state = self.read_readonly()
            sources = [deepcopy(state["sources"].get(key, {"id": key, "state": "missing"})) for key in page.source_ids]
            job = deepcopy(state["repairs"].get(page.name))
            result = page.to_index_entry()
            result.update(content=page.content, sources=sources, links=list(dict.fromkeys(page.related + wiki_links(page.content))),
                          backlinks=self.memory.backlinks(page.name), repair=job, legacy_baseline=page.name in state["baselines"])
            if job and job["status"] in ("pending", "processing", "failed"):
                result["status"] = job["status"]
            return result
