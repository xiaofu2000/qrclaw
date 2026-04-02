"""
MemoryExtractionNode —— 会话记忆提取节点（Claude Code 风格）

对齐 Claude Code 的 Session Memory 设计：

核心机制：
1. 阈值驱动：Token 数量 + 工具调用次数
2. LLM 分析：默默分析会话，提取关键信息
3. 延迟写入：不阻塞主流程

不同于 Claude Code：
- 使用后台线程而非 forking（Python 限制）
"""
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from qrclaw.logger import get_logger
from qrclaw.memory import LongTermMemory, MemoryType

logger = get_logger("qrclaw.graph.nodes.memory_extraction")


# ── 配置常量（参考 Claude Code）─────────────────────────────────────────────────

@dataclass
class ExtractionConfig:
    """提取配置"""

    # 初始化阈值：Token 数量达到多少时开始提取
    # 支持环境变量覆盖：MEMORY_INIT_THRESHOLD
    minimum_message_tokens_to_init: int = 10000

    # 更新间隔：Token 增长多少时触发下一次提取
    # 支持环境变量覆盖：MEMORY_UPDATE_INTERVAL
    minimum_tokens_between_update: int = 5000

    # 工具调用次数间隔
    # 支持环境变量覆盖：MEMORY_TOOL_CALL_INTERVAL
    tool_calls_between_updates: int = 3

    # 最大待处理数量
    max_pending: int = 3

    def __post_init__(self):
        """从环境变量加载配置（如果设置了）"""
        if "MEMORY_INIT_THRESHOLD" in os.environ:
            self.minimum_message_tokens_to_init = int(os.environ["MEMORY_INIT_THRESHOLD"])
        if "MEMORY_UPDATE_INTERVAL" in os.environ:
            self.minimum_tokens_between_update = int(os.environ["MEMORY_UPDATE_INTERVAL"])
        if "MEMORY_TOOL_CALL_INTERVAL" in os.environ:
            self.tool_calls_between_updates = int(os.environ["MEMORY_TOOL_CALL_INTERVAL"])


DEFAULT_CONFIG = ExtractionConfig()


# ── 提取结果 ──────────────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    """提取结果"""

    content: str
    memory_type: MemoryType
    description: str


# ── 提取提示词模板 ────────────────────────────────────────────────────────────

EXTRACTION_PROMPT_TEMPLATE = """请分析以下会话，提取值得记住的关键信息。

要求：
1. 识别用户偏好和习惯（如工具偏好、沟通方式）
2. 识别反馈和纠正（用户告诉你要做什么/不要做什么）
3. 识别项目上下文（任务目标、约束条件、决策）
4. 如果没有值得记住的信息，请回复"无需提取"

输出格式：
如果需要提取，请按以下格式输出：

类型: USER | FEEDBACK | PROJECT | REFERENCE
描述: 一句话描述
内容: 详细说明（2-3句话）

---

会话内容：
{messages_text}"""


# ── MemoryExtractionNode ─────────────────────────────────────────────────────

class MemoryExtractionNode:
    """
    会话记忆提取节点

    对齐 Claude Code Session Memory：
    - 阈值驱动（Token + 工具调用）
    - LLM 主动分析
    - 延迟批量写入

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
        memory: LongTermMemory,
        config: ExtractionConfig = None,
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
        self._pending_extractions: list[ExtractionResult] = []
        self._pending_lock = threading.Lock()

        # 后台线程
        self._bg_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._session_ref = None

        logger.info(
            f"MemoryExtractionNode 初始化完成 | "
            f"init_threshold={self.config.minimum_message_tokens_to_init} | "
            f"update_interval={self.config.minimum_tokens_between_update} | "
            f"tool_call_interval={self.config.tool_calls_between_updates}"
        )

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
                logger.debug(
                    f"[记忆提取] 未初始化 | 当前token={token_count} < 阈值={self.config.minimum_message_tokens_to_init}"
                )
                return False
            self._is_initialized = True
            logger.info(f"[记忆提取] 已初始化，当前Token数: {token_count}")

        # Token 增长阈值
        tokens_since_last = token_count - self._tokens_at_last_extraction
        if tokens_since_last < self.config.minimum_tokens_between_update:
            logger.debug(
                f"[记忆提取] Token增长不足 | {tokens_since_last} < {self.config.minimum_tokens_between_update}"
            )
            return False

        # 工具调用阈值
        tool_calls = self._count_tool_calls_since(messages)
        if tool_calls < self.config.tool_calls_between_updates:
            # 检查最后一条消息是否有工具调用（自然间隙）
            if self._has_tool_calls_in_last_turn(messages):
                logger.debug(
                    f"[记忆提取] 工具调用不足且最后轮次有工具调用 | "
                    f"tool_calls={tool_calls} < {self.config.tool_calls_between_updates}"
                )
                return False

        logger.info(
            f"[记忆提取] ✅ 触发提取 | token={token_count} | "
            f"tokens_since_last={tokens_since_last} | tool_calls={tool_calls}"
        )
        return True

    def _count_tool_calls_since(self, messages: list, since_uuid: str = None) -> int:
        """计算指定消息后的工具调用次数"""
        count = 0
        found_start = since_uuid is None

        for msg in messages:
            if not found_start:
                if hasattr(msg, 'uuid') and msg.uuid == since_uuid:
                    found_start = True
                continue

            if hasattr(msg, 'type') and msg.type == 'assistant':
                content = getattr(msg, 'content', None) or getattr(msg, 'message', {}).get('content', [])
                if isinstance(content, list):
                    count += sum(1 for block in content if block.get('type') == 'tool_use')

        return count

    def _has_tool_calls_in_last_turn(self, messages: list) -> bool:
        """
        检查最后一条 assistant 消息是否有工具调用

        Returns:
            True: 最后有 assistant 且有工具调用
            False: 没有 assistant 消息，或 assistant 没有工具调用
        """
        # 从后往前找最后一条 assistant 消息
        for msg in reversed(messages):
            if hasattr(msg, 'type') and msg.type == 'assistant':
                content = getattr(msg, 'content', None) or getattr(msg, 'message', {}).get('content', [])
                if isinstance(content, list):
                    return any(block.get('type') == 'tool_use' for block in content)
                # assistant 但内容为空，没有工具调用
                return False

        # 没有找到 assistant 消息，返回 False（允许提取）
        return False

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
            if hasattr(last_msg, 'uuid') and last_msg.uuid:
                self._last_message_uuid = last_msg.uuid

        # 启动提取
        self._trigger_extraction(messages)

        return True

    def _trigger_extraction(self, messages: list):
        """触发提取"""
        # 将消息转换为文本格式
        messages_text = self._format_messages_for_llm(messages)

        # 构建提取提示词
        prompt = self._build_extraction_prompt(messages_text)

        # 加入待处理队列
        with self._pending_lock:
            self._pending_extractions.append(
                ExtractionResult(
                    content=prompt,
                    memory_type=MemoryType.PROJECT,
                    description=f"会话记忆_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                )
            )
            pending_count = len(self._pending_extractions)

        logger.warning(f"[记忆提取] 任务已加入队列，消息数: {len(messages)}，pending: {pending_count}")

    def _format_messages_for_llm(self, messages: list) -> str:
        """将消息列表格式化为 LLM 可读的文本"""
        lines = []
        for msg in messages[-20:]:  # 最近20条
            role = getattr(msg, 'type', 'unknown') or getattr(msg, 'role', 'unknown')
            content = msg.get('content', '') if isinstance(msg, dict) else getattr(msg, 'content', '')
            if isinstance(content, list):
                content = '\n'.join(
                    b.get('text', '') or b.get('content', '')
                    for b in content if b.get('type') == 'text'
                )
            lines.append(f"[{role}] {content}")
        return '\n\n'.join(lines)

    def _build_extraction_prompt(self, messages_text: str) -> str:
        """构建提取提示词"""
        return EXTRACTION_PROMPT_TEMPLATE.format(messages_text=messages_text)

    # ── 批量写入 ──────────────────────────────────────────────────────────────

    def flush_pending(self) -> int:
        """
        批量写入待处理的记忆

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

        logger.warning(f"[记忆提取] 开始写入，待处理: {len(to_write)}，剩余: {remaining}")

        success_count = 0
        for result in to_write:
            try:
                # 使用 LLM 分析
                analysis = self._analyze_with_llm(result.content)
                logger.warning(f"[记忆提取] 是否需要提取: {analysis}")

                if analysis and analysis.strip() != "无需提取":
                    memory_info = self._parse_llm_response(analysis)
                    if memory_info:
                        success = self.memory.save_entry(
                            name=memory_info.get('name', f"记忆_{datetime.now().strftime('%H%M%S')}"),
                            description=memory_info.get('description', ''),
                            content=memory_info.get('content', ''),
                            memory_type=MemoryType.from_str(memory_info.get('type', 'project').lower()),
                        )
                        if success:
                            success_count += 1
                            logger.warning(f"[记忆提取] 写入成功: {memory_info.get('name')}")
                        else:
                            logger.warning(f"[记忆提取] 写入失败: {memory_info.get('name')}")
                else:
                    logger.debug("[记忆提取] LLM 判定无需提取")
            except Exception as e:
                logger.warning(f"[记忆提取] 写入异常: {e}")

        if success_count > 0:
            logger.info(f"[记忆提取] 批量写入完成: {success_count}/{len(to_write)}")

        return success_count

    def _analyze_with_llm(self, prompt: str) -> str:
        """
        使用 LLM 分析会话内容

        接入 QRClaw 的 LLM Provider
        """
        try:
            from qrclaw.providers import provider

            messages = [{"role": "user", "content": prompt}]
            logger.debug("[记忆提取] 调用 LLM 分析...")
            response = provider.chat(messages)
            logger.debug(f"[记忆提取] LLM 响应: {response.content[:100]}...")

            return response.content

        except Exception as e:
            logger.warning(f"[记忆提取] LLM 分析失败: {e}，跳过本次提取")
            return "无需提取"

    def _parse_llm_response(self, response: str) -> Optional[dict]:
        """解析 LLM 返回的内容"""
        result = {}

        for line in response.strip().split('\n'):
            line = line.strip()
            if line.startswith('类型:'):
                result['type'] = line[3:].strip()
            elif line.startswith('描述:'):
                result['description'] = line[3:].strip()
            elif line.startswith('内容:'):
                result['content'] = line[3:].strip()
            elif line.startswith('名称:') or line.startswith('name:'):
                result['name'] = line.split(':', 1)[1].strip()

        # 生成默认名称
        if 'name' not in result:
            result['name'] = f"会话记忆_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        return result if result.get('type') else None

    # ── 后台线程 ─────────────────────────────────────────────────────────────

    def start_background_thread(self, session, interval_seconds: int = 60):
        """启动后台提取线程"""
        if self._bg_thread and self._bg_thread.is_alive():
            logger.warning("后台提取线程已在运行")
            return

        self._session_ref = session
        self._stop_event.clear()

        self._bg_thread = threading.Thread(
            target=self._background_loop,
            args=(interval_seconds,),
            name="memory-extraction",
            daemon=True,
        )
        self._bg_thread.start()
        logger.info("启动后台记忆提取线程")

    def stop_background_thread(self):
        """停止后台提取线程"""
        self._stop_event.set()
        if self._bg_thread:
            self._bg_thread.join(timeout=5)
        logger.info("停止后台记忆提取线程")

    def _background_loop(self, interval_seconds: int):
        """后台循环"""
        while not self._stop_event.is_set():
            try:
                # 检查是否需要提取
                if self._session_ref:
                    messages = getattr(self._session_ref, 'messages', [])
                    token_count = self._estimate_tokens(messages)

                    self.check_and_extract(messages, token_count)

                # 写入待处理的记忆
                if self._pending_extractions:
                    self.flush_pending()

            except Exception as e:
                logger.warning(f"后台提取异常: {e}")

            self._stop_event.wait(interval_seconds)

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

        集成到 ReactLoopNode.run() 的循环结束后
        """
        messages = getattr(session, 'messages', [])
        token_count = self.extractor._estimate_tokens(messages)

        logger.warning(
            f"[记忆提取集成] 轮次={current_round} | "
            f"消息数={len(messages)} | 估算token={token_count}"
        )

        self.extractor.check_and_extract(messages, token_count, current_round)

        # 检查是否需要批量写入
        if self.extractor.get_pending_count() > self.extractor.config.max_pending:
            self.extractor.flush_pending()
