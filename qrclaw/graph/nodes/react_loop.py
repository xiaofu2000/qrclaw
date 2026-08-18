"""
ReactLoop 节点

职责：主 agent 的 ReAct 循环，工具调用 + LLM 推理直到得出最终答案。
适用于：简单任务直接执行、并行计划完成后的最终整合。
"""
import json
from typing import Callable, Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from qrclaw.config import MAX_ITERATIONS
from qrclaw.tools.registry import get_schemas, get_schemas_for_sub_agent
from qrclaw.memory.context.session import Session
from qrclaw.memory.context.context_manager import get_context_manager
from qrclaw.llm_service import LLMService, get_llm_service
from qrclaw.workspace import Workspace
from qrclaw.logger import get_logger
from qrclaw.graph.nodes.message_codec import assistant_response_to_message, tool_result_to_message
from qrclaw.graph.nodes.tool_runner import ToolRunner
from qrclaw.execution.context import RunCancelled

logger = get_logger("qrclaw.graph.nodes.react_loop")


def run_react_loop(
    messages: list[dict],
    tools: list[dict],
    max_iterations: int = MAX_ITERATIONS,
    on_tool_call: Optional[Callable[[str, str], str]] = None,
    on_finish: Optional[Callable[[str], None]] = None,
    on_assistant_message: Optional[Callable[[dict], None]] = None,
    console: Optional[Console] = None,
    silent: bool = False,
    session: Optional[Session] = None,
    llm_service: Optional[LLMService] = None,
    execution_context=None,
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
        console: Rich Console，silent=True 时不输出
        silent: 静默模式，不打印任何内容
        session: Session 对象，用于更新 token 使用量

    Returns:
        最终 LLM 输出内容
    """
    _console = console or Console()
    llm = llm_service or get_llm_service()

    for iteration in range(max_iterations):
        logger.debug(f"run_react_loop 第 {iteration + 1} 轮")

        if execution_context is not None:
            execution_context.check_cancelled()
            execution_context.publish(
                "agent.progress",
                {"current_action": f"正在进行第 {iteration + 1} 轮模型调用"},
            )

        streamed_parts: list[str] = []
        message_id = None
        if execution_context is not None:
            message_id = execution_context.new_id("msg")

        def _on_delta(delta: str) -> None:
            """把模型文本增量立即发布给当前运行。"""

            if execution_context is None or not message_id or not delta:
                return
            execution_context.check_cancelled()
            streamed_parts.append(delta)
            execution_context.publish(
                "assistant.delta",
                {"message_id": message_id, "delta": delta},
            )

        with _console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots") if not silent else _noop_ctx():
            try:
                chat_kwargs = {"messages": messages, "tools": tools}
                if execution_context is not None:
                    chat_kwargs["on_delta"] = _on_delta
                response = llm.chat(**chat_kwargs)
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
        if execution_context is not None:
            execution_context.publish(
                "usage.updated",
                {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                },
            )

        if response.finish_reason == "stop":
            if execution_context is not None:
                if response.content and not streamed_parts:
                    _on_delta(response.content)
                execution_context.publish(
                    "assistant.completed",
                    {"message_id": message_id, "content": response.content or ""},
                )
                execution_context.assistant_message_id = message_id
                execution_context.assistant_completed = True
                execution_context.publish(
                    "agent.progress",
                    {
                        "current_action": "正在整理最终结果",
                        "decision_summary": "当前执行路径已经完成，准备返回可验证的结果",
                    },
                )
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
            if execution_context is not None:
                execution_context.check_cancelled()
            name = tc.name
            arguments = tc.arguments
            logger.info(f"工具调用: {name}")
            if execution_context is not None:
                execution_context.publish(
                    "agent.progress",
                    {
                        "current_action": f"准备调用工具 {name}",
                        "decision_summary": f"模型选择调用 {name} 继续完成当前任务",
                    },
                )

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
                messages.append(assistant_response_to_message(response))
                assistant_msg_saved = True

            try:
                if on_tool_call:
                    result = on_tool_call(name, arguments)
                else:
                    result = ToolRunner().run(name, arguments)
            except RunCancelled:
                raise
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

            messages.append(tool_result_to_message(tc.id, result))

    logger.warning("run_react_loop 达到最大迭代次数")
    return "错误：达到最大迭代次数"


class _noop_ctx:
    """静默模式占位 context manager"""
    def __enter__(self): return self
    def __exit__(self, *_): pass


class ReactLoopNode:

    def __init__(self):
        # 延迟导入，避免循环依赖
        self._memory_integration = None

    def _get_memory_integration(self, workspace: Workspace):
        """懒加载 MemoryExtractionIntegration（仅主 agent）"""
        if self._memory_integration is None:
            from qrclaw.graph.nodes.memory_extraction import (
                MemoryExtractionNode,
                MemoryExtractionIntegration,
            )
            from qrclaw.agent import register_extractor
            from qrclaw.memory.wiki import WikiMemory

            if workspace:
                memory = WikiMemory.for_workspace(workspace.memory_dir)
                extractor = MemoryExtractionNode(memory)
                register_extractor(extractor)
                self._memory_integration = MemoryExtractionIntegration(extractor)
                logger.debug("MemoryExtractionIntegration 已初始化")
        return self._memory_integration

    def run(
        self,
        session: Session,
        console: Console,
        workspace: Workspace,
        auto_confirm: bool = False,
        is_sub_agent: bool = False,
        execution_context=None,
    ) -> str:
        ctx = get_context_manager()

        # 仅主 agent 启用记忆提取
        memory_integration = self._get_memory_integration(workspace) if not is_sub_agent else None

        tools = get_schemas_for_sub_agent() if is_sub_agent else get_schemas()
        tool_runner = ToolRunner(
            console=console,
            auto_confirm=auto_confirm,
            execution_context=execution_context,
        )

        def _on_assistant_message(message: dict) -> None:
            session.add(dict(message))
            if memory_integration:
                memory_integration.on_react_loop_end(session, 0)

        # 调用前检查压缩
        ctx.compress_if_needed()
        messages = ctx.build_messages("react")

        try:
            result = run_react_loop(
                messages=messages,
                tools=tools,
                max_iterations=MAX_ITERATIONS,
                on_tool_call=tool_runner.run,
                on_assistant_message=_on_assistant_message,
                console=console,
                silent=False,
                session=session,
                llm_service=get_llm_service(),
                execution_context=execution_context,
            )
        except RunCancelled:
            raise
        except RuntimeError as e:
            console.print(Panel(
                f"[bold red]{e}[/bold red]",
                title="[bold red]⛔ 权限不足[/bold red]",
                border_style="red",
                expand=False,
            ))
            return f"错误：{e}"

        return result
