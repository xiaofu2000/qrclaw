"""
ReactLoop 节点

职责：主 agent 的 ReAct 循环，工具调用 + LLM 推理直到得出最终答案。
适用于：简单任务直接执行、并行计划完成后的最终整合。
"""
import json
import threading
from typing import Callable, Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from qrclaw.config import MAX_ITERATIONS, _MODEL_MAX_TOKENS, COMPRESS_THRESHOLD
from qrclaw.tools.registry import get_schemas, get_schemas_for_sub_agent
from qrclaw.memory.context.session import Session
from qrclaw.memory.context.session import SessionEvent
from qrclaw.memory.context.context_manager import get_context_manager
from qrclaw.llm_service import LLMService, get_llm_service
from qrclaw.workspace import Workspace
from qrclaw.logger import get_logger
from qrclaw.graph.nodes.message_codec import assistant_response_to_message, tool_result_to_message
from qrclaw.graph.nodes.tool_runner import ToolRunner

logger = get_logger("qrclaw.graph.nodes.react_loop")


def run_react_loop(
    messages: list[dict],
    tools: list[dict],
    max_iterations: int = MAX_ITERATIONS,
    on_tool_call: Optional[Callable[[str, str], str]] = None,
    on_finish: Optional[Callable[[str], None]] = None,
    on_assistant_message: Optional[Callable[[dict], None]] = None,
    on_tool_result: Optional[Callable[[dict], None]] = None,
    console: Optional[Console] = None,
    silent: bool = False,
    session: Optional[Session] = None,
    llm_service: Optional[LLMService] = None,
    on_delta: Optional[Callable[[str], None]] = None,
) -> str:
    """
    通用 ReAct 循环核心，供 ReactLoopNode 和 MemoryExtractionNode 等复用。

    Args:
        messages: 初始消息列表（system + 任务描述）
        tools: 工具 schema 列表
        max_iterations: 最大迭代次数
        on_tool_call: 工具执行回调，签名 (name, arguments) -> result
                      为 None 时使用默认 execute()
        on_finish: 完成回调，签名 (content) -> None
        on_assistant_message: 完整 assistant 消息回调，包含 reasoning_content/tool_calls
        on_tool_result: 工具结果消息回调，用于保存会话历史
        console: Rich Console，silent=True 时不输出
        silent: 静默模式，不打印任何内容
        session: Session 对象，用于更新 token 使用量
        on_delta: 主 Agent 可见正文的逐块回调

    Returns:
        最终 LLM 输出内容
    """
    _console = console or Console()
    llm = llm_service or get_llm_service()

    for iteration in range(max_iterations):
        logger.debug(f"run_react_loop 第 {iteration + 1} 轮")

        with _console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots") if not silent else _noop_ctx():
            try:
                chat_kwargs = {"tools": tools}
                if on_delta is not None:
                    chat_kwargs["on_delta"] = on_delta
                response = llm.chat(messages, **chat_kwargs)
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}", exc_info=True)
                raise

        # 更新 token 使用量（每次 LLM 调用后都更新）
        if session:
            session.update_tokens(
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                total_tokens=response.total_tokens,
            )

        if response.finish_reason == "stop":
            if not silent:
                _console.print()
                _console.print(Panel(
                    Markdown(response.content or "", code_theme="ansi_dark"),
                    title="[bold green]Agent[/bold green]",
                    border_style="green",
                    expand=True,
                ))
                _console.print()
            assistant_msg = assistant_response_to_message(response)
            messages.append(assistant_msg)
            if on_assistant_message:
                on_assistant_message(assistant_msg)
            if on_finish:
                on_finish(response.content or "")
            return response.content or ""

        if response.finish_reason == "length":
            return "错误：回复被截断"

        assistant_msg_saved = False

        for tc in response.tool_calls:
            name = tc.name
            arguments = tc.arguments
            logger.info(f"工具调用: {name}")

            if not silent:
                try:
                    args_formatted = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
                except Exception:
                    args_formatted = arguments
                _console.print()
                _console.print(Panel(
                    Text.assemble(("[bold cyan]" + name + "[/bold cyan]\n", ""), (args_formatted, "")),
                    title="[bold yellow]▶ 调用工具[/bold yellow]",
                    border_style="yellow",
                    expand=False,
                ))
                _console.print()

            if not assistant_msg_saved:
                assistant_msg = assistant_response_to_message(response)
                messages.append(assistant_msg)
                if on_assistant_message:
                    on_assistant_message(assistant_msg)
                assistant_msg_saved = True

            try:
                if on_tool_call:
                    result = on_tool_call(name, arguments)
                else:
                    result = ToolRunner().run(name, arguments)
            except Exception as e:
                result = f"工具执行失败: {str(e)}"
                logger.error(f"工具执行失败: {name}, 错误: {e}", exc_info=True)

            if not silent:
                preview = result[:200] + "..." if len(result) > 200 else result
                _console.print(Panel(
                    Text(preview),
                    title="[bold blue]◀ 工具结果[/bold blue]",
                    border_style="blue",
                    expand=False,
                ))
                _console.print()

            tool_msg = tool_result_to_message(tc.id, result)
            messages.append(tool_msg)
            if on_tool_result:
                on_tool_result(tool_msg)

    logger.warning("run_react_loop 达到最大迭代次数")
    return "错误：达到最大迭代次数"


class _noop_ctx:
    """静默模式占位 context manager"""
    def __enter__(self): return self
    def __exit__(self, *_): pass


class ReactLoopNode:

    def __init__(self):
        self._memory_integrations = {}
        self._memory_lock = threading.Lock()

    def _get_memory_integration(self, workspace: Workspace, session: Session):
        """懒加载 MemoryExtractionIntegration（仅主 agent）"""
        key = (workspace.memory_dir.resolve(), session.session_id)
        with self._memory_lock:
            if key not in self._memory_integrations or self._memory_integrations[key]._closed:
                from qrclaw.graph.nodes.memory_extraction import MemoryExtractionNode, MemoryExtractionIntegration
                from qrclaw.memory.wiki import WikiMemory
                memory = WikiMemory.for_workspace(workspace.memory_dir)
                extractor = MemoryExtractionNode(memory, session=session)
                self._memory_integrations[key] = MemoryExtractionIntegration(extractor)
                logger.debug("MemoryExtractionIntegration 已初始化")
            return self._memory_integrations[key]

    def resume_memory_extractions(self, workspace: Workspace) -> None:
        """服务启动时恢复已有主会话的待处理正文和闲置计时。"""
        from qrclaw.memory.context.session import list_sessions
        from qrclaw.memory.wiki.extraction import ExtractionIndexer
        for summary in list_sessions(workspace.sessions_dir):
            session_id = summary["id"]
            if not ExtractionIndexer(workspace.memory_dir, session_id).state_file.exists():
                continue
            try:
                session = Session(workspace.sessions_dir, session_id)
                self._get_memory_integration(workspace, session).resume()
            except Exception as exc:
                logger.warning("恢复会话记忆提取失败，会话 %s：%s", session_id, exc, exc_info=True)

    def stop_memory_extractions(self, workspace: Workspace, session_id: str | None = None) -> None:
        """关闭服务或删除会话时取消其闲置任务，保留正在执行的写入进度。"""
        with self._memory_lock:
            for (memory_dir, key), integration in self._memory_integrations.items():
                if memory_dir == workspace.memory_dir.resolve() and (session_id is None or key == session_id):
                    integration.close()

    def run(
        self,
        session: Session,
        console: Console,
        workspace: Workspace,
        auto_confirm: bool = False,
        is_sub_agent: bool = False,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> str:
        """执行当前会话的 ReAct 循环，可选地转发正文片段。"""
        ctx = get_context_manager()

        # 仅主 agent 启用记忆提取
        memory_integration = self._get_memory_integration(workspace, session) if not is_sub_agent else None
        if memory_integration:
            from qrclaw.agent import register_extractor
            register_extractor(memory_integration.extractor)

        tools = get_schemas_for_sub_agent() if is_sub_agent else get_schemas()
        tool_runner = ToolRunner(console=console, auto_confirm=auto_confirm)

        def run_tool(name: str, arguments: str) -> str:
            """记录工具状态，不把参数或结果写入过程事件。"""
            session.add_event(SessionEvent(kind="tool_status", title="工具开始", content=name,
                                           meta={"state": "started", "tool": name}))
            try:
                result = tool_runner.run(name, arguments)
            except Exception:
                session.add_event(SessionEvent(kind="tool_status", title="工具失败", content=name,
                                               meta={"state": "failed", "tool": name}))
                raise
            failed = getattr(tool_runner, "last_error", False)
            session.add_event(SessionEvent(kind="tool_status", title="工具失败" if failed else "工具完成",
                                           content=name, meta={"state": "failed" if failed else "completed", "tool": name}))
            return result

        def _on_assistant_message(message: dict) -> None:
            session.add(dict(message))
            if is_sub_agent:
                from qrclaw.subagent_progress import clear_sub_agent_text
                clear_sub_agent_text(session.session_id)
            session.add_event(SessionEvent(kind="message_saved", title="消息已保存"))

        def _on_tool_result(message: dict) -> None:
            session.add(dict(message))
            session.add_event(SessionEvent(kind="message_saved", title="消息已保存"))

        # 调用前检查压缩
        ctx.compress_if_needed()
        messages = ctx.build_messages("react")
        session.add_event(SessionEvent(kind="context_usage", title="上下文用量",
            meta={"tokens": session.context_tokens, "limit": _MODEL_MAX_TOKENS,
                  "compression_threshold": COMPRESS_THRESHOLD}))

        try:
            result = run_react_loop(
                messages=messages,
                tools=tools,
                max_iterations=MAX_ITERATIONS,
                on_tool_call=run_tool,
                on_assistant_message=_on_assistant_message,
                on_tool_result=_on_tool_result,
                # 由整次主请求统一收尾，Plan 中间阶段不启动闲置任务。
                console=console,
                silent=False,
                session=session,
                llm_service=get_llm_service(),
                on_delta=on_delta,
            )
        except RuntimeError as e:
            console.print(Panel(
                f"[bold red]{e}[/bold red]",
                title="[bold red]⛔ 权限不足[/bold red]",
                border_style="red",
                expand=False,
            ))
            return f"错误：{e}"

        return result
