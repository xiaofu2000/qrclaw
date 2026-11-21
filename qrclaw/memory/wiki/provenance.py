"""工作空间级记忆来源、撤销标记和可恢复的页面整理队列。"""
from __future__ import annotations

from copy import deepcopy
from difflib import unified_diff
import hashlib
import json
import time
import uuid

from qrclaw.memory.wiki.index import atomic_write
from qrclaw.memory.wiki.extraction.strategies import filter_memory_messages
from qrclaw.memory.wiki.page import wiki_links


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


class WikiProvenance:
    """复用 Wiki 文件锁，原子维护来源及队列，避免撤销与保存交错。"""

    def __init__(self, memory):
        """每个工作空间使用独立状态文件。"""
        self.memory = memory
        self.path = memory.memory_dir / ".wiki-state.json"

    def read(self) -> dict:
        """调用方持有 Wiki 锁；损坏状态明确报错，绝不自动覆盖。"""
        if not self.path.exists():
            return {"sources": {}, "deleted_pages": {}, "repairs": {}, "baselines": {}, "page_revisions": {}, "session_changes": {}, "last_activity": 0}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data.setdefault("page_revisions", {})
            data.setdefault("session_changes", {})
            for key in ("sources", "deleted_pages", "repairs", "baselines", "page_revisions", "session_changes"):
                if not isinstance(data[key], dict):
                    raise ValueError(key)
            # 单调序号避免同一秒的聊天与手动修改无法确定先后；旧记录按原时间迁移。
            for order, source in enumerate(sorted(data["sources"].values(), key=lambda s: s["captured_at"]), 1):
                source.setdefault("order", order)
            data["source_order"] = max(data.get("source_order", 0), max((s["order"] for s in data["sources"].values()), default=0))
            return data
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("Wiki 来源进度损坏，请检查 .wiki-state.json") from exc

    def write(self, state: dict) -> None:
        """调用方持有 Wiki 锁；单文件原子替换保留所有未完成任务。"""
        atomic_write(self.path, json.dumps(state, ensure_ascii=False, indent=2))

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
            self.write(state)
        return captured

    def invalidate(self, session_id: str, message_ids: set[str] | None = None, *, session_change: dict | None = None) -> None:
        """清除来源正文并排队受影响页面；整会话删除无需保留聊天原文。"""
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
            for page in self.memory.list_pages(include_unavailable=True):
                if invalid.intersection(page.source_ids):
                    self._queue(state, page.name, "聊天来源已编辑或删除")
            # 队列先落盘，读取路径也检查来源状态，崩溃不会暴露失效正文。
            self.write(state)
            for page in self.memory.list_pages(include_unavailable=True):
                if invalid.intersection(page.source_ids):
                    self.memory.save_page(page.name, page.content, page.description, page.tags, page.related,
                                          source_ids=page.source_ids, status="pending", allow_invalid_sources=True)
        self.memory.invalidate_cache()
        self.memory.repair_worker.resume()

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
        sources = self.read()["sources"]
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
            state = self.read()
            sources = [deepcopy(state["sources"].get(key, {"id": key, "state": "missing"})) for key in page.source_ids]
            job = deepcopy(state["repairs"].get(page.name))
            result = page.to_index_entry()
            result.update(content=page.content, sources=sources, links=list(dict.fromkeys(page.related + wiki_links(page.content))),
                          backlinks=self.memory.backlinks(page.name), repair=job, legacy_baseline=page.name in state["baselines"])
            if job and job["status"] in ("pending", "processing", "failed"):
                result["status"] = job["status"]
            return result
