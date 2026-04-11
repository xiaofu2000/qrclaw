"""
MemoryExtractionNode —— 会话记忆提取节点（LLM Wiki 风格）

触发逻辑不变（Token 阈值 + 工具调用次数），
写入方式升级为 LLM Wiki：
- 提取时注入 index.md，让 LLM 了解已有页面
- LLM 决定新建页面还是更新已有页面
- 写入通过 WikiMemory.save_page 完成（upsert 语义）
"""
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from qrclaw.logger import get_logger
from qrclaw.memory.wiki import WikiMemory

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


# ── Pydantic Schema（强制结构化输出）─────────────────────────────────────────

class WikiPageSchema(BaseModel):
    """单个 Wiki 页面操作，每个实例只聚焦一个主题"""
    action: Literal["create", "update"] = Field(description="create=新建页面，update=更新已有页面")
    name: str = Field(description="页面名称，一个名称只对应一个主题，update 时必须与索引中完全一致")
    content: str = Field(description="页面完整正文，Markdown 格式，只包含本页面主题的内容，用 [[页面名]] 引用其他主题")
    description: str = Field(default="", description="一句话描述，显示在索引里")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    related: list[str] = Field(default_factory=list, description="关联页面名列表，只填索引中已存在的页面名")


class ExtractionSchema(BaseModel):
    """记忆提取结果"""
    needs_update: bool = Field(description="对话中是否有值得写入 Wiki 的知识")
    pages: list[WikiPageSchema] = Field(
        default_factory=list,
        description="要写入的页面列表。内容涉及多个主题时必须拆成多个页面分别列出，每个页面只聚焦一个独立主题，不要把所有内容塞进一个页面"
    )


# ── 提取结果 ──────────────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    """提取结果（待写入队列条目）"""
    prompt: str  # 传给 LLM 的完整提示词


# ── 提取提示词模板 ────────────────────────────────────────────────────────────

EXTRACTION_PROMPT_TEMPLATE = """你是一个 Wiki 知识库维护者。请分析以下会话，决定是否需要更新或新建 Wiki 页面。

【现有 Wiki 页面索引】
{index_md}

【最近对话】
{messages_text}

【任务】
判断对话中是否有值得长期保存的知识（用户偏好、项目配置、技术决策、行为反馈等）。

【写入规则】
1. 每个页面只聚焦一个主题，不要把多个主题塞进一个页面
2. 内容涉及多个主题时，拆分成多个独立页面，每个页面用 [[页面名]] 引用相关页面
3. related 字段只填【现有 Wiki 页面索引】中已存在的页面名，不能引用不存在的页面
4. 对于要 update 的页面，name 必须与索引中完全一致
5. content 是页面完整正文，不是增量

【输出格式】
{{
  "needs_update": true,
  "pages": [
    {{
      "action": "create",
      "name": "页面名称（一个主题一个页面）",
      "content": "页面完整正文（Markdown，用 [[页面名]] 引用其他页面）",
      "description": "一句话描述",
      "tags": ["标签1", "标签2"],
      "related": ["只填已存在的页面名"]
    }}
  ]
}}

needs_update 为 false 时，pages 为空数组。"""


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
        memory: WikiMemory,
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

        # 工具调用阈值：只统计上次提取之后的工具调用数
        tool_calls = self._count_tool_calls_since(messages, since_uuid=self._last_message_uuid)
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

    def _count_tool_calls_since(self, messages: list, since_uuid: str = None) -> int:
        """计算指定消息后的工具调用次数"""
        count = 0
        found_start = since_uuid is None

        for msg in messages:
            # uuid 定位起始点
            if not found_start:
                msg_uuid = msg.get('uuid') if isinstance(msg, dict) else getattr(msg, 'uuid', None)
                if msg_uuid == since_uuid:
                    found_start = True
                continue

            # 兼容 dict（OpenAI格式）和对象两种结构
            role = msg.get('role') if isinstance(msg, dict) else getattr(msg, 'type', None)
            if role != 'assistant':
                continue

            if isinstance(msg, dict):
                # OpenAI 格式：tool_calls 是独立字段
                tool_calls = msg.get('tool_calls') or []
                count += len(tool_calls)
            else:
                # Anthropic 对象格式：content 里有 tool_use block
                content = getattr(msg, 'content', []) or []
                if isinstance(content, list):
                    count += sum(1 for block in content if isinstance(block, dict) and block.get('type') == 'tool_use')

        return count

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
        """触发提取：把消息文本 + index.md 一起打包进队列"""
        messages_text = self._format_messages_for_llm(messages)
        # 只传页面名+描述，不传路径/标题等噪音
        entries = self.memory.index.all_entries()
        if entries:
            index_summary = "\n".join(
                f"- {e['name']}：{e.get('description', '')}" for e in entries
            )
        else:
            index_summary = "（暂无页面）"

        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            index_md=index_summary,
            messages_text=messages_text,
        )
        with self._pending_lock:
            self._pending_extractions.append(ExtractionResult(prompt=prompt))
            pending_count = len(self._pending_extractions)

        logger.warning(f"[记忆提取] 任务已加入队列，消息数: {len(messages)}，pending: {pending_count}")

    def _format_messages_for_llm(self, messages: list) -> str:
        """
        将消息列表格式化为 LLM 可读的文本。

        过滤规则：
        - 跳过 role=tool 的消息（工具返回结果）
        - 跳过含 tool_calls 的 assistant 消息（中间推理步骤）
        - 保留 role=user 和纯文字 role=assistant 消息（[SUMMARY] 摘要也保留）
        - 传全量，不截断
        """
        lines = []
        for msg in messages:
            role = msg.get('role', '') if isinstance(msg, dict) else getattr(msg, 'role', '')

            # 跳过 tool 返回
            if role == 'tool':
                continue

            # 跳过含 tool_calls 的 assistant 消息
            if role == 'assistant':
                tool_calls = msg.get('tool_calls') if isinstance(msg, dict) else getattr(msg, 'tool_calls', None)
                if tool_calls:
                    continue

            content = msg.get('content', '') if isinstance(msg, dict) else getattr(msg, 'content', '')
            if not content:
                continue

            if isinstance(content, list):
                content = '\n'.join(
                    b.get('text', '') or b.get('content', '')
                    for b in content if b.get('type') == 'text'
                )

            if content.strip():
                lines.append(f"[{role}] {content}")

        return '\n\n'.join(lines)

    def _build_extraction_prompt(self, messages_text: str) -> str:
        """兼容旧调用，不再使用"""
        return messages_text

    # ── 批量写入 ──────────────────────────────────────────────────────────────

    def flush_pending(self) -> int:
        """
        批量写入待处理的记忆（记忆 Agent：最多2轮 ReAct）

        第1轮：LLM 分析会话 + index摘要，决定 create/update，如有 update 调 read_wiki_page
        第2轮（可选）：LLM 拿到现有内容后合并，调 submit_memory_result 提交 JSON
        脚本执行 save_page()，LLM 不直接写文件

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
                count = self._run_memory_agent(result.prompt)
                success_count += count
            except Exception as e:
                logger.warning(f"[记忆提取] 写入异常: {e}")

        if success_count > 0:
            logger.info(f"[记忆提取] 批量写入完成: {success_count}/{len(to_write)}")
            try:
                from qrclaw.memory.context.context_manager import get_context_manager
                get_context_manager().invalidate_cache()
            except Exception:
                pass

        return success_count

    def _run_memory_agent(self, prompt: str) -> int:
        """
        用 run_react_loop 跑记忆 Agent（最多2轮，静默）。

        工具：
          - read_wiki_page：读现有页面内容（update 时第1轮调用）
          - submit_memory_result：提交最终 JSON，由脚本写入文件

        Returns:
            int: 成功写入的页面数
        """
        from qrclaw.graph.nodes.react_loop import run_react_loop
        from qrclaw.tools.registry import get_schema_by_name

        submitted_pages: list = []

        def _tool_call(name: str, arguments: str) -> str:
            import json as _json
            args = _json.loads(arguments)

            if name == "read_wiki_page":
                page = self.memory.get_page(args["name"])
                if not page:
                    return f"页面「{args['name']}」不存在"
                return f"# {page.name}\n\n{page.content}"

            if name == "submit_memory_result":
                pages_data = args.get("pages", [])
                for p in pages_data:
                    try:
                        self.memory.save_page(
                            name=p["name"],
                            content=p["content"],
                            description=p.get("description", ""),
                            tags=p.get("tags", []),
                            related=p.get("related", []),
                        )
                        submitted_pages.append(p["name"])
                        logger.warning(f"[记忆提取] 写入成功: [{p.get('action','create')}] {p['name']}")
                    except Exception as e:
                        logger.warning(f"[记忆提取] 写入页面失败: {p.get('name')}, {e}")
                return f"已写入 {len(submitted_pages)} 个页面"

            return f"未知工具: {name}"

        # 工具 schema：只给记忆 Agent 两个工具
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_wiki_page",
                    "description": "读取指定 Wiki 页面的现有完整内容，update 时必须先读再合并",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string", "description": "页面名称"}},
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_memory_result",
                    "description": "提交最终记忆写入结果，由系统执行文件写入",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pages": {
                                "type": "array",
                                "description": "要写入的页面列表",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "action":      {"type": "string", "enum": ["create", "update"]},
                                        "name":        {"type": "string"},
                                        "content":     {"type": "string"},
                                        "description": {"type": "string"},
                                        "tags":        {"type": "array", "items": {"type": "string"}},
                                        "related":     {"type": "array", "items": {"type": "string"}},
                                    },
                                    "required": ["action", "name", "content"],
                                },
                            }
                        },
                        "required": ["pages"],
                    },
                },
            },
        ]

        messages = [{"role": "user", "content": prompt}]

        try:
            run_react_loop(
                messages=messages,
                tools=tools,
                max_iterations=2,
                on_tool_call=_tool_call,
                silent=True,
            )
        except Exception as e:
            logger.warning(f"[记忆提取] 记忆 Agent 执行失败: {e}")

        return len(submitted_pages)



    def _analyze_with_llm(self, prompt: str) -> Optional[ExtractionSchema]:
        """
        使用 instructor + Pydantic Schema 强制结构化输出，与 Router 节点一致。
        失败返回 None（跳过本次提取）。
        """
        try:
            import instructor
            from litellm import completion
            from qrclaw.providers import provider
            from qrclaw.providers.litellm_provider import LiteLLMProvider

            if not isinstance(provider, LiteLLMProvider):
                raise RuntimeError("记忆提取目前仅支持 LiteLLMProvider")

            client = instructor.from_litellm(completion, mode=instructor.Mode.JSON)
            messages = [{"role": "user", "content": prompt}]
            kwargs = provider.make_instructor_kwargs(messages, temperature=0.1)
            kwargs["response_model"] = ExtractionSchema
            kwargs["max_retries"] = 3

            result: ExtractionSchema = client.chat.completions.create(**kwargs)
            logger.debug(f"[记忆提取] needs_update={result.needs_update}, pages={len(result.pages)}")
            return result

        except Exception as e:
            logger.warning(f"[记忆提取] LLM 分析失败: {e}，跳过本次提取")
            return None

    def _parse_llm_response(self, response: str) -> list[dict]:
        """兼容旧调用，不再使用"""
        return []

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
        check_and_extract 判断是否需要提取，flush_pending 在后台线程执行，不阻塞 CLI
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
                name="memory-extraction",
            )
            t.start()


# ── 全局 Extractor 注册表（供工具层访问）────────────────────────────────────────

_global_extractor: Optional[MemoryExtractionNode] = None


def register_extractor(extractor: MemoryExtractionNode) -> None:
    """ReactLoopNode 初始化时注册 extractor，供工具层调用"""
    global _global_extractor
    _global_extractor = extractor


def get_extractor() -> Optional[MemoryExtractionNode]:
    """工具层获取当前 extractor"""
    return _global_extractor
