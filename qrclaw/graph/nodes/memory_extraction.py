"""主 Agent 的自动记忆批处理及主动写入，按会话持久化提取进度。"""
from copy import deepcopy
import hashlib
import threading
from threading import Thread, Timer
import time

from qrclaw.logger import get_logger
from qrclaw.memory.wiki import WikiMemory
from qrclaw.memory.wiki.extraction import ExtractionConfig, ExtractionIndexer, ExtractionRunner
from qrclaw.memory.wiki.extraction.config import DEFAULT_CONFIG
from qrclaw.memory.wiki.extraction.strategies import filter_memory_messages, format_messages_for_llm

logger = get_logger("qrclaw.graph.nodes.memory_extraction")


def _signature(message: dict) -> str:
    """识别同一消息的正文版本，编辑后允许重新提取。"""
    return hashlib.sha256(f"{message['role']}\0{message['content']}".encode()).hexdigest()


def _completed_turns(messages: list[dict]) -> int:
    """按用户消息后出现助手正文计算完整轮次，不统计工具往返。"""
    turns = 0
    waiting = False
    for message in messages:
        if message["role"] == "user":
            waiting = True
        elif waiting:
            turns += 1
            waiting = False
    return turns


class MemoryExtractionNode:
    """累计有效正文，闲置或长对话时批量提取；主动写入立即执行。"""

    def __init__(self, memory: WikiMemory, config: ExtractionConfig = None, llm_analyzer=None, session=None):
        """真实会话绑定独立进度文件，测试或旧调用可不绑定会话。"""
        self.memory = memory
        self.config = config or DEFAULT_CONFIG
        self.session = session
        self.indexer = ExtractionIndexer(memory.memory_dir, session.session_id if session else None)
        self._runner = None
        self._llm_analyzer = llm_analyzer
        self._flush_lock = threading.Lock()

    def _get_runner(self) -> ExtractionRunner:
        """复用现有的页面定位、旧正文合并和保存流程。"""
        if self._runner is None:
            self._runner = ExtractionRunner(self.memory, self.config, self._llm_analyzer)
        return self._runner

    def collect(self, messages: list, *, preserve: bool = False) -> None:
        """合并未处理正文；压缩前保留原文，普通同步移除已删改的版本。"""
        current = filter_memory_messages(messages)
        versions = {m["uuid"]: _signature(m) for m in current}

        def change(state):
            retained = set(state.preserved_message_ids)
            state.pending_messages = [m for m in state.pending_messages
                if (m["uuid"] in retained and m["uuid"] not in versions)
                or versions.get(m["uuid"]) == _signature(m)]
            changed = False
            for message in current:
                key = message["uuid"]
                if state.seen_messages.get(key) != versions[key]:
                    state.pending_messages.append(message)
                    state.seen_messages[key] = versions[key]
                    changed = True
            if changed:
                state.last_activity_time = time.time()
            if preserve and state.pending_messages:
                state.preserved_message_ids = [m["uuid"] for m in state.pending_messages]
                state.force_pending = True
            else:
                pending_ids = {m["uuid"] for m in state.pending_messages}
                state.preserved_message_ids = [key for key in retained if key in pending_ids]
        self.indexer.update(change)

    def note_activity(self) -> None:
        """用户开始新请求时更新闲置起点，延后自动提取。"""
        self.indexer.update(lambda state: setattr(state, "last_activity_time", time.time()))

    def discard_messages(self, message_ids: set[str]) -> None:
        """编辑或删除一轮消息时撤销对应的待处理快照。"""
        def change(state):
            state.pending_messages = [m for m in state.pending_messages if m["uuid"] not in message_ids]
            state.preserved_message_ids = [key for key in state.preserved_message_ids if key not in message_ids]
            for key in message_ids:
                state.seen_messages.pop(key, None)
        self.indexer.update(change)

    def next_delay(self) -> float | None:
        """返回距离下一次自动提取的时间；内容不足时不创建计时任务。"""
        state = self.indexer.get_state()
        if not state.pending_messages:
            return None
        threshold = (self.config.minimum_message_tokens_to_init if state.total_extractions == 0
                     else self.config.minimum_tokens_between_update)
        long_conversation = self._estimate_tokens(state.pending_messages) >= threshold
        if not state.force_pending and not long_conversation and _completed_turns(state.pending_messages) < self.config.minimum_turns:
            return None
        now = time.time()
        cooldown_end = state.last_attempt_time + self.config.cooldown_seconds if state.last_attempt_time else 0
        due = cooldown_end if long_conversation or state.force_pending else max(
            cooldown_end, state.last_activity_time + self.config.idle_seconds)
        return max(0, due - now)

    def should_extract(self, messages: list = None, token_count: int = None) -> bool:
        """按未处理正文及闲置、冷却状态判断，外部历史 token 计数不参与决策。"""
        delay = self.next_delay()
        return delay is not None and delay <= 0

    def check_and_extract(self, messages: list, token_count: int = None, current_round: int = 0) -> bool:
        """接收新增内容并判断是否到期；不足阈值的内容也会持久化。"""
        self.collect(messages)
        return self.should_extract()

    def preserve_before_compression(self, messages: list) -> None:
        """压缩丢弃旧消息前保存未处理原文，后台仍按冷却策略整理。"""
        self.collect(messages, preserve=True)

    def write_now(self, content: str) -> int:
        """主动记忆立即调用模型，不受自动提取的轮次、闲置和冷却限制。"""
        with self._flush_lock, self.indexer.worker_lock:
            runner = self._get_runner()
            extraction = runner.analyze_with_llm({
                "index_summary": runner.build_index_summary(), "messages_text": content,
            })
            if extraction is None:
                raise RuntimeError("记忆分析失败")
            return runner.process_extraction_result(extraction)

    def _batch_is_current(self, batch: list[dict]) -> bool:
        """写入前检查会话和消息版本，阻止已删改消息的旧结果落盘。"""
        if self.session is not None and not self.session._path.exists():
            return False
        pending = {m["uuid"]: _signature(m) for m in self.indexer.get_state().pending_messages}
        return all(pending.get(m["uuid"]) == _signature(m) for m in batch)

    def flush_pending(self, *, automatic: bool = False) -> int:
        """处理一个合并批次；成功或无需更新才清除，失败时原文留在磁盘。"""
        with self._flush_lock, self.indexer.worker_lock:
            if self.session is not None and not self.session._path.exists():
                return 0
            # 多个进程争用同一批次时，拿到工作锁后仍须重新检查冷却。
            if automatic and not self.should_extract():
                return 0
            batch = deepcopy(self.indexer.get_state().pending_messages)
            if not batch:
                return 0
            self.indexer.update(lambda state: setattr(state, "last_attempt_time", time.time()))
            runner = self._get_runner()
            try:
                extraction = runner.analyze_with_llm({
                    # 执行时再取索引，避免闲置期间索引已变化。
                    "index_summary": runner.build_index_summary(),
                    "messages_text": format_messages_for_llm(batch),
                })
                if extraction is None:
                    raise RuntimeError("记忆分析失败")
                if not self._batch_is_current(batch):
                    logger.info("[记忆提取] 来源消息已删改，放弃旧结果")
                    return 0
                def save_current(**page):
                    """来源检查与页面保存共享短锁，模型生成正文时不持有此锁。"""
                    with self.indexer.lock:
                        if not self._batch_is_current(batch):
                            return False
                        return self.memory.save_page(**page)

                written = runner.process_extraction_result(extraction,
                    is_current=lambda: self._batch_is_current(batch), save_page=save_current)
                if not self._batch_is_current(batch):
                    return written
                completed = {m["uuid"]: _signature(m) for m in batch}

                def acknowledge(state):
                    if self.session is not None and not self.session._path.exists():
                        return
                    pending = {m["uuid"]: _signature(m) for m in state.pending_messages}
                    if any(pending.get(key) != value for key, value in completed.items()):
                        return
                    state.pending_messages = [m for m in state.pending_messages
                        if completed.get(m["uuid"]) != _signature(m)]
                    pending_ids = {m["uuid"] for m in state.pending_messages}
                    state.preserved_message_ids = [key for key in state.preserved_message_ids if key in pending_ids]
                    state.force_pending = bool(state.preserved_message_ids)
                    state.total_extractions += 1
                    state.last_extraction_tokens += self._estimate_tokens(batch)
                    state.last_extraction_time = time.time()
                self.indexer.update(acknowledge)
                logger.info("[记忆提取] 批次处理完成，正文消息 %s 条，写入 %s 个页面", len(batch), written)
                return written
            except Exception as exc:
                logger.warning("[记忆提取] 处理失败，保留正文等待冷却后重试：%s", exc, exc_info=True)
                if self.session is None or self.session._path.exists():
                    def retry(state):
                        if self.session is not None and not self.session._path.exists():
                            return
                        state.force_pending = True
                        # 测试或自定义零冷却也不能形成即时重试循环。
                        state.last_attempt_time = max(state.last_attempt_time, time.time() + max(0, 1 - self.config.cooldown_seconds))
                    self.indexer.update(retry)
                return 0

    def _estimate_tokens(self, messages: list) -> int:
        """只估算实际送给提取模型的正文，沿用字符数除以三的估算。"""
        return sum(max(1, len(m["content"]) // 3) for m in filter_memory_messages(messages))

    def get_pending_count(self) -> int:
        """同一会话只保留一个合并批次，返回零或一。"""
        return int(bool(self.indexer.get_state().pending_messages))

    def get_stats(self) -> dict:
        """读取已持久化的成功次数、待处理正文及上次执行时间。"""
        state = self.indexer.get_state()
        return {"total_extractions": state.total_extractions,
                "pending_count": int(bool(state.pending_messages)),
                "pending_tokens": self._estimate_tokens(state.pending_messages),
                "pending_turns": _completed_turns(state.pending_messages),
                "last_extraction_time": state.last_extraction_time}


class MemoryExtractionIntegration:
    """管理每个会话的闲置计时与后台写入线程，运行中延后自动提取。"""

    def __init__(self, extractor: MemoryExtractionNode):
        """计时器可随进程退出，实际写入任务继续使用后台工作线程。"""
        self.extractor = extractor
        self._timer = None
        self._worker = None
        self._lock = threading.RLock()
        self._busy = False
        self._closed = False

    def on_run_start(self) -> None:
        """整次主 Agent 请求开始时取消闲置计时，包括路由及 Plan 执行阶段。"""
        with self._lock:
            self._busy = True
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self.extractor.note_activity()

    def on_react_loop_end(self, session, current_round: int = 0) -> None:
        """主请求结束后合并正文，并安排长对话兜底或闲置提取。"""
        with self._lock:
            self._busy = False
            self.extractor.collect(session.messages)
            self._schedule()

    def resume(self) -> None:
        """服务重启或恢复会话后续接磁盘上的待处理批次。"""
        with self._lock:
            if self.extractor.session is not None:
                self.extractor.collect(self.extractor.session.messages)
            self._schedule()

    def close(self) -> None:
        """取消闲置计时；未完成批次仍在磁盘上等待后续恢复。"""
        with self._lock:
            self._closed = True
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _schedule(self) -> None:
        """只安排一个计时器或工作线程，新消息可重新安排闲置时间。"""
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._busy or self._closed or (self._worker and self._worker.is_alive()):
            return
        if self.extractor.session is not None and not self.extractor.session._path.exists():
            self.close()
            self.extractor.indexer.discard()
            return
        delay = self.extractor.next_delay()
        if delay is None:
            return
        timer = Timer(max(0.01, delay), lambda: self._start_worker(timer))
        self._timer = timer
        self._timer.daemon = True
        self._timer.start()

    def _start_worker(self, timer=None) -> None:
        """到期后再次检查冷却与运行状态，避免过期计时器启动重复任务。"""
        with self._lock:
            if timer is not None and timer is not self._timer:
                return
            self._timer = None
            if self._busy or self._closed or (self._worker and self._worker.is_alive()):
                return
            if not self.extractor.should_extract():
                self._schedule()
                return
            self._worker = Thread(target=self._flush, daemon=False, name="记忆提取后台写入")
            self._worker.start()

    def _flush(self) -> None:
        """执行一次批次；写入期间收到的新正文留给后续批次。"""
        try:
            self.extractor.flush_pending(automatic=True)
        except Exception as exc:
            logger.warning("[记忆提取] 后台任务异常，持久化进度已保留：%s", exc, exc_info=True)
        finally:
            with self._lock:
                self._worker = None
                self._schedule()
