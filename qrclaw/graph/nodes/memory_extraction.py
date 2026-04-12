"""
MemoryExtractionNode —— 会话记忆提取节点（LLM Wiki 风格）

重构后职责划分：
- Node 层：编排调用，只负责状态追踪、队列管理
- wiki/extraction/ 包：提示词、Schema、阈值策略、LLM 调用

触发逻辑（Token 阈值 + 工具调用次数）：
- 提取时注入 index.md，让 LLM 了解已有页面
- LLM 决定新建页面还是更新已有页面
- 写入通过 WikiMemory.save_page 完成（upsert 语义）

使用方式：
    # 1. 初始化
    extractor = MemoryExtractionNode(memory, config)

    # 2. ReAct 循环结束时检查
    extractor.check_and_extract(session.messages, current_round)

    # 3. 需要时批量写入（可在后台线程执行）
    extractor.flush_pending()
"""
import threading
from typing import Optional

from qrclaw.logger import get_logger
from qrclaw.memory.wiki import WikiMemory
from qrclaw.memory.wiki.extraction import (
    ExtractionConfig,
    WikiPageSchema,
    ExtractionSchema,
    ExtractionRunner,
)

# WikiLLMAnalyzer 延迟导入（需要 instructor 依赖）
try:
    from qrclaw.memory.wiki.extraction.llm import WikiLLMAnalyzer
except ImportError:
    WikiLLMAnalyzer = None  # type: ignore
from qrclaw.memory.wiki.extraction.strategies import (
    count_tool_calls_since,
    get_messages_since_last_extraction,
)
from qrclaw.memory.wiki.extraction.config import DEFAULT_CONFIG

logger = get_logger("qrclaw.graph.nodes.memory_extraction")


# ── MemoryExtractionNode ─────────────────────────────────────────────────────

class MemoryExtractionNode:
    """
    会话记忆提取节点

    对齐 Claude Code Session Memory：
    - 阈值驱动（Token + 工具调用）
    - LLM 主动分析
    - 延迟批量写入

    职责划分（重构后）：
    - Node：编排职责（状态追踪、队列管理、线程控制）
    - ExtractionRunner：纯逻辑（LLM 调用、消息格式化、提取策略）

    使用方式：
    ```python
    # 1. 初始化
    extractor = MemoryExtractionNode(memory, config)

    # 2. ReAct 循环结束时检查
    extractor.check_and_extract(session.messages, current_round)

    # 3. 启动后台提取线程
    extractor.start_background_thread(session)
    ```
    """

    def __init__(
        self,
        memory: WikiMemory,
        config: ExtractionConfig = None,
        llm_analyzer: WikiLLMAnalyzer = None,
    ):
        self.memory = memory
        self.config = config or DEFAULT_CONFIG

        # 状态追踪
        self._tokens_at_last_extraction: int = 0
        self._last_message_uuid: Optional[str] = None
        self._is_initialized: bool = False

        # 冷却追踪：name -> 上次提取的时间戳
        self._cooldown_tracker: dict[str, float] = {}
        self._lock = threading.Lock()

        # 待写入队列
        self._pending_extractions: list[dict] = []
        self._pending_lock = threading.Lock()

        # 提取逻辑执行器（委托给 Runner）
        self._runner: Optional[ExtractionRunner] = None

        logger.info(
            f"MemoryExtractionNode 初始化完成 | "
            f"init_threshold={self.config.minimum_message_tokens_to_init} | "
            f"update_interval={self.config.minimum_tokens_between_update} | "
            f"tool_call_interval={self.config.tool_calls_between_updates}"
        )

    def _get_runner(self) -> ExtractionRunner:
        """懒加载获取 Runner"""
        if self._runner is None:
            self._runner = ExtractionRunner(
                wiki_memory=self.memory,
                config=self.config,
                llm_analyzer=None,  # Runner 内部懒加载
            )
        return self._runner

    # ── 阈值检查 ──────────────────────────────────────────────────────────────

    def should_extract(self, messages: list, token_count: int) -> bool:
        """
        检查是否应该触发提取

        参考 Claude Code shouldExtractMemory()：
        1. Token 阈值必须满足（初始化 + 更新间隔）
        2. 工具调用阈值 OR 最后一条消息无工具调用（自然间隙）
        """
        # 初始化检查
        if not self._is_initialized:
            if token_count < self.config.minimum_message_tokens_to_init:
                logger.warning(
                    f"[记忆提取] 未初始化 | 当前token={token_count} < 阈值={self.config.minimum_message_tokens_to_init}"
                )
                return False
            self._is_initialized = True
            logger.info(f"[记忆提取] 已初始化，当前Token数: {token_count}")

        # Token 增长阈值
        tokens_since_last = token_count - self._tokens_at_last_extraction
        if tokens_since_last < self.config.minimum_tokens_between_update:
            logger.warning(
                f"[记忆提取] Token增长不足 | {tokens_since_last} < {self.config.minimum_tokens_between_update}"
            )
            return False

        # 工具调用阈值：委托给 strategies 模块统计
        tool_calls = count_tool_calls_since(messages, since_uuid=self._last_message_uuid)
        if tool_calls < self.config.tool_calls_between_updates:
            logger.warning(
                f"[记忆提取] 工具调用不足 | {tool_calls} < {self.config.tool_calls_between_updates}"
            )
            return False

        logger.warning(
            f"[记忆提取] ✅ 触发提取 | token={token_count} | "
            f"tokens_since_last={tokens_since_last} | tool_calls_since_last={tool_calls}"
        )
        return True

    # ── 检查与提取 ───────────────────────────────────────────────────────────

    def check_and_extract(
        self,
        messages: list,
        token_count: int,
        current_round: int = 0,
    ) -> bool:
        """
        检查是否应该提取，并在需要时启动提取

        Args:
            messages: 对话历史
            token_count: 当前 token 数
            current_round: 当前轮次

        Returns:
            bool: 是否触发了提取
        """
        if not self.should_extract(messages, token_count):
            return False

        # 更新状态
        self._tokens_at_last_extraction = token_count
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, dict):
                self._last_message_uuid = last_msg.get('uuid')
            else:
                self._last_message_uuid = getattr(last_msg, 'uuid', None)

        # 启动提取（委托给 Runner）
        self._trigger_extraction(messages)

        return True

    def _trigger_extraction(self, messages: list):
        """触发提取：委托给 Runner 构建提取数据并加入队列"""
        runner = self._get_runner()
        extraction_data = runner.trigger_extraction(
            messages=messages,
            last_message_uuid=self._last_message_uuid,
        )

        with self._pending_lock:
            self._pending_extractions.append(extraction_data)
            pending_count = len(self._pending_extractions)

        logger.warning(f"[记忆提取] 任务已加入队列，pending: {pending_count}")

    # ── 批量写入 ──────────────────────────────────────────────────────────────

    def flush_pending(self) -> int:
        """
        批量写入待处理的记忆（委托给 Runner）

        create → save_page()（新建）
        append → append_page()（追加到已有页面末尾，不读现有内容，省 token）

        Returns:
            int: 成功写入的数量
        """
        with self._pending_lock:
            if not self._pending_extractions:
                logger.debug("[记忆提取] flush_pending: 无待处理任务")
                return 0

            to_write = self._pending_extractions[:self.config.max_pending]
            self._pending_extractions = self._pending_extractions[self.config.max_pending:]
            remaining = len(self._pending_extractions)

        logger.warning(f"[记忆提取] 开始写入，待处理任务: {len(to_write)}，剩余: {remaining}")

        success_count = 0
        total_pages = 0
        runner = self._get_runner()

        for extraction_data in to_write:
            try:
                # LLM 分析（委托给 Runner）
                extraction = runner.analyze_with_llm(extraction_data)

                if extraction is None:
                    logger.warning("[记忆提取] LLM 分析返回 None，跳过")
                    continue
                if not extraction.needs_update:
                    logger.info("[记忆提取] LLM 判定无需更新")
                    continue
                if not extraction.pages:
                    logger.info("[记忆提取] LLM 返回页面列表为空")
                    continue

                total_pages += len(extraction.pages)

                # 处理提取结果（委托给 Runner）
                written = runner.process_extraction_result(extraction)
                success_count += written

            except Exception as e:
                logger.warning(f"[记忆提取] 写入异常: {e}", exc_info=True)

        if success_count > 0:
            logger.info(f"[记忆提取] 批量写入完成: {success_count}/{total_pages} 页面")

            # 刷新上下文缓存
            try:
                from qrclaw.memory.context.context_manager import get_context_manager
                get_context_manager().invalidate_cache()
            except Exception:
                pass

        return success_count

    # ── Token 估算 ───────────────────────────────────────────────────────────

    def _estimate_tokens(self, messages: list) -> int:
        """
        估算 token 数量

        改进估算：
        - 中文按 2 字符 ≈ 1 token
        - 英文按 4 字符 ≈ 1 token
        - 包含 JSON/tool calls 时更密集
        """
        total = 0
        for msg in messages:
            # 安全获取 content，处理 None 和非字符串情况
            if isinstance(msg, dict):
                content = msg.get('content', '')
            else:
                content = getattr(msg, 'content', '')
            if content is None or not isinstance(content, (str, list)):
                continue
            if isinstance(content, list):
                for block in content:
                    if block.get('type') == 'text':
                        text = block.get('text', '')
                        total += self._count_text_tokens(text)
                    elif block.get('type') == 'tool_use':
                        # 工具调用内容更密集
                        import json
                        args = block.get('input', {})
                        total += len(json.dumps(args)) // 2
            else:
                total += self._count_text_tokens(content)
        return total

    def _count_text_tokens(self, text: str) -> int:
        """估算文本的 token 数"""
        if not text:
            return 0
        # 简单估算：字符数 / 3（考虑中英文混合）
        return max(1, len(text) // 3)

    # ── 状态查询 ─────────────────────────────────────────────────────────────

    def get_pending_count(self) -> int:
        """获取待写入数量"""
        with self._pending_lock:
            return len(self._pending_extractions)

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "initialized": self._is_initialized,
            "tokens_at_last_extraction": self._tokens_at_last_extraction,
            "last_message_uuid": self._last_message_uuid,
            "pending_count": self.get_pending_count(),
            "cooldown_count": len(self._cooldown_tracker),
        }


# ── 与 Graph 集成的辅助类 ───────────────────────────────────────────────────

class MemoryExtractionIntegration:
    """
    记忆提取与 Graph 的集成辅助类

    使用方式：
    ```python
    integration = MemoryExtractionIntegration(extractor)

    # 在 ReactLoopNode 循环结束时调用
    integration.on_react_loop_end(session, current_round)
    ```
    """

    def __init__(self, extractor: MemoryExtractionNode):
        self.extractor = extractor

    def on_react_loop_end(
        self,
        session,
        current_round: int = 0,
    ) -> None:
        """
        在 ReAct 循环结束时调用

        集成到 ReactLoopNode.run() 的循环结束后：
        1. check_and_extract 判断是否需要提取（加入待处理队列）
        2. 如果待处理队列达到阈值，启动后台线程执行 flush_pending，不阻塞 CLI
        """
        messages = getattr(session, 'messages', [])
        token_count = self.extractor._estimate_tokens(messages)

        logger.debug(
            f"[记忆提取集成] 轮次={current_round} | "
            f"消息数={len(messages)} | 估算token={token_count}"
        )

        self.extractor.check_and_extract(messages, token_count, current_round)

        # 检查是否需要批量写入，放后台线程执行，不阻塞主线程
        if self.extractor.get_pending_count() >= self.extractor.config.max_pending:
            t = threading.Thread(
                target=self.extractor.flush_pending,
                daemon=True,
                name="memory-extraction-flush",
            )
            t.start()
