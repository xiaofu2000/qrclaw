"""闲置期间根据有效来源重建页面，持久化进度支持失败和重启恢复。"""
from __future__ import annotations

from copy import deepcopy
import json
import threading
from threading import Timer, Thread
import time

from filelock import FileLock, Timeout

from qrclaw.logger import get_logger
from qrclaw.memory.wiki.extraction.config import DEFAULT_CONFIG
from qrclaw.memory.wiki.extraction.runner import ExtractionRunner
from qrclaw.memory.wiki.provenance import source_text

logger = get_logger("qrclaw.memory.wiki.repair")


class WikiRepairWorker:
    """工作空间单队列；模型生成时不持有 Wiki 锁，提交时校验任务和页面版本。"""

    def __init__(self, memory, config=None, analyzer=None):
        """沿用自动提取配置及合并模型，测试可注入确定性的分析器。"""
        self.memory = memory
        self.config = config or DEFAULT_CONFIG
        self.runner = ExtractionRunner(memory, self.config, analyzer)
        self.worker_lock = FileLock(str(memory.memory_dir / ".wiki-repair.worker.lock"))
        self.lock = threading.RLock()
        self.timer = None
        self.worker = None
        self.closed = False
        self.busy_sessions = set()

    def activity(self, session_id: str, busy: bool) -> None:
        """新请求及排队都暂停整理；所有主请求结束后重新计算闲置时间。"""
        with self.lock:
            if busy:
                self.busy_sessions.add(session_id)
            else:
                self.busy_sessions.discard(session_id)
            with self.memory._lock:
                state = self.memory.provenance.read()
                state["last_activity"] = time.time()
                self.memory.provenance.write(state)
            self.resume()

    def next_delay(self) -> float | None:
        """不要求三轮对话，撤销来源仍受闲置和失败冷却约束。"""
        with self.memory._lock:
            state = self.memory.provenance.read()
            jobs = [j for j in state["repairs"].values() if j["status"] in ("pending", "failed", "processing")]
            if not jobs:
                return None
            due = min(max(state.get("last_activity", 0) + self.config.idle_seconds,
                          j.get("last_attempt", 0) + self.config.cooldown_seconds) for j in jobs)
            return max(0, due - time.time())

    def resume(self) -> None:
        """恢复磁盘任务并续接计时；过期计时器不会启动另一个工作线程。"""
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if self.closed or self.busy_sessions or self.worker and self.worker.is_alive():
                return
            delay = self.next_delay()
            if delay is None:
                return
            timer = Timer(max(0.05, delay), lambda: self._start(timer))
            timer.daemon = True
            self.timer = timer
            timer.start()

    def _start(self, timer) -> None:
        """只响应当前计时器，并再次检查闲置和冷却。"""
        with self.lock:
            if timer is not self.timer or self.closed or self.busy_sessions:
                return
            self.timer = None
            delay = self.next_delay()
            if delay is None or delay > 0:
                self.resume()
                return
            self.worker = Thread(target=self._run, name="Wiki 记忆整理", daemon=False)
            self.worker.start()

    def _run(self) -> None:
        """处理到期任务；失败仍保留队列，后续按冷却重试。"""
        try:
            self.process_due()
        except Exception:
            logger.exception("Wiki 后台整理异常，进度已保留")
        finally:
            with self.lock:
                self.worker = None
                self.resume()

    def process_due(self, *, force=False) -> int:
        """同步处理用于工作线程及测试；force 仅供内部验证，不通过 API 暴露。"""
        try:
            with self.worker_lock.acquire(timeout=0):
                return self._process_due(force)
        except Timeout:
            return 0

    def _process_due(self, force: bool) -> int:
        """任务快照和提交分别持短锁，中间调用模型不阻塞用户编辑。"""
        completed = 0
        with self.memory._lock:
            names = list(self.memory.provenance.read()["repairs"])
        for name in names:
            if self.busy_sessions or self.closed:
                break
            with self.memory._lock:
                state = self.memory.provenance.read()
                if state["session_changes"]:
                    break
                job = state["repairs"].get(name)
                if not job or job["status"] not in ("pending", "failed", "processing"):
                    continue
                if not force and time.time() < max(state.get("last_activity", 0) + self.config.idle_seconds,
                                                   job.get("last_attempt", 0) + self.config.cooldown_seconds):
                    continue
                page = self.memory.get_page(name)
                if page is None or name in state["deleted_pages"]:
                    state["repairs"].pop(name, None)
                    self.memory.provenance.write(state)
                    continue
                if f"repair:{job['id']}" in page.applied_batches:
                    job.update(status="completed", error="", finished_at=time.time())
                    self.memory.provenance.write(state)
                    completed += 1
                    continue
                job["status"] = "processing"
                job["last_attempt"] = time.time()
                self.memory.provenance.write(state)
                job_id = job["id"]
                sources = [deepcopy(state["sources"][key]) for key in page.source_ids
                           if state["sources"].get(key, {}).get("state") == "valid"]
                # 编辑后的消息仍属同一来源；重建时使用它的新版本，不等待下一轮批量提取。
                for key in page.source_ids:
                    old = state["sources"].get(key, {})
                    if old.get("state") != "withdrawn" or old.get("kind") != "chat":
                        continue
                    replacements = [s for s in state["sources"].values() if s.get("state") == "valid"
                        and s.get("session_id") == old.get("session_id") and s.get("message_id") == old.get("message_id")]
                    if replacements:
                        sources.append(deepcopy(max(replacements, key=lambda s: s["order"])))
                sources = list({s["id"]: s for s in sources}.values())
                sources.sort(key=lambda source: source["order"])
                baseline = deepcopy(state["baselines"].get(name))
                if any(s.get("kind") == "manual" for s in sources):
                    baseline = None
            try:
                if not sources and not baseline:
                    content, description, tags, related, status = "", "来源已撤销", [], [], "inactive"
                else:
                    if self.runner.llm_analyzer is None:
                        self.runner._init_llm_analyzer()
                    evidence = source_text(sources)
                    if baseline:
                        evidence = "[历史基线：来源未追溯]\n" + json.dumps(baseline, ensure_ascii=False) + "\n\n" + evidence
                    result = self.runner.llm_analyzer.consolidate(existing_content="",
                        new_content="仅根据以下有效证据重建，不能补回撤销的事实。用户完整替换页面是对旧事实的明确纠正。\n" + evidence,
                        page_name=name)
                    content = result.content.strip()
                    if not content:
                        raise ValueError("整理结果为空，保留待整理页面")
                    description = result.description
                    valid_metadata = ([baseline] if baseline else []) + sources
                    tags = result.tags if "tags" in result.model_fields_set else list(dict.fromkeys(tag for s in valid_metadata for tag in s.get("tags", [])))
                    related = result.related if "related" in result.model_fields_set else list(dict.fromkeys(link for s in valid_metadata for link in s.get("related", [])))
                    status = "ready" if sources else "legacy"
                with self.memory._lock:
                    latest = self.memory.provenance.read()
                    current_job = latest["repairs"].get(name)
                    if not current_job or current_job["id"] != job_id or name in latest["deleted_pages"]:
                        continue
                    if not self.memory.provenance.current([s["id"] for s in sources]):
                        continue
                    saved = self.memory.save_page(name, content, description, tags, related, expected_page=page,
                        source_ids=[s["id"] for s in sources], status=status, batch_id=f"repair:{job_id}")
                    current_job.update(status="completed", error="", finished_at=time.time())
                    current_job["records"] = (current_job.get("records", []) + [{"status": "completed", "at": time.time(),
                        "revision": saved.revision, "source_ids": saved.source_ids}])[-50:]
                    self.memory.provenance.write(latest)
                    self.memory.index.append_log("来源整理完成", name, f"任务 {job_id}；版本 {saved.revision}；来源 {', '.join(saved.source_ids)}")
                    completed += 1
                    logger.info("Wiki 来源整理完成：%s，版本 %s", name, saved.revision)
            except Exception as exc:
                with self.memory._lock:
                    latest = self.memory.provenance.read()
                    current_job = latest["repairs"].get(name)
                    if current_job and current_job["id"] == job_id:
                        current_job.update(status="failed", error=str(exc))
                        current_job["last_attempt"] = max(current_job["last_attempt"], time.time() + max(0, 1 - self.config.cooldown_seconds))
                        current_job["records"] = (current_job.get("records", []) + [{"status": "failed", "at": time.time(), "error": str(exc)}])[-50:]
                        self.memory.provenance.write(latest)
                logger.warning("Wiki 来源整理失败：%s，保留进度等待重试：%s", name, exc)
        return completed

    def close(self) -> None:
        """停止尚未开始的计时；运行中的模型提交前仍检查页面和来源版本。"""
        with self.lock:
            self.closed = True
            if self.timer:
                self.timer.cancel()
                self.timer = None
