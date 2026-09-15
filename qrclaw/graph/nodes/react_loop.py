"""
ReactLoop 节点

职责：主 agent 的 ReAct 循环，工具调用 + LLM 推理直到得出最终答案。
适用于：简单任务直接执行、并行计划完成后的最终整合。
"""
import json
from contextlib import nullcontext
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
    console: Optional[Console] = None,
    silent: bool = False,
    session: Optional[Session] = None,
    llm_service: Optional[LLMService] = None,
    execution_context=None,
    context_manager=None,
) -> str:
    """
    ReAct 循环核心，每次模型请求前重新组装上下文，每条消息立即持久化。

    Args:
        messages: 初始消息列表（system + 任务描述）
        tools: 工具 schema 列表
        max_iterations: 最大迭代次数
        on_tool_call: 工具执行回调，签名 (name, arguments) -> result
                      为 None 时使用默认 execute()
        console: Rich Console，silent=True 时不输出
        silent: 静默模式，不打印任何内容
        session: 会话消息与 Token 使用量的持久化入口
        context_manager: 每轮重新构建并检查实际输入预算

    Returns:
        最终 LLM 输出内容
    """
    _console = console or Console()
    llm = llm_service or get_llm_service()

    def record(message: dict) -> None:
        """统一记录模型回复和工具结果，避免推理历史与磁盘会话分叉。"""
        if session is not None:
            session.add(message)
        messages.append(message)

    for iteration in range(max_iterations):
        logger.debug(f"run_react_loop 第 {iteration + 1} 轮")

        if execution_context is not None:
            execution_context.check_cancelled()
            execution_context.publish(
                "agent.progress",
                {"current_action": f"正在进行第 {iteration + 1} 轮模型调用"},
            )

        if context_manager is not None:
            messages = context_manager.build_messages("react", tools=tools)

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

        with _console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots") if not silent else nullcontext():
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
            record(assistant_msg)
            return response.content or ""

        if response.finish_reason == "length":
            return "错误：回复被截断"

        if not response.tool_calls:
            raise ValueError(f"模型未返回最终回复或工具调用：{response.finish_reason}")
        record(assistant_response_to_message(response))
        completed = set()

        try:
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

                record(tool_result_to_message(tc.id, result))
                completed.add(tc.id)
        finally:
            # 取消或异常也补齐未执行的结果，恢复会话时不会出现悬空工具调用。
            for tc in response.tool_calls:
                if tc.id not in completed:
                    record(tool_result_to_message(tc.id, "工具执行中断，未获得可确认的结果，请检查实际状态后继续。"))

    logger.warning("run_react_loop 达到最大迭代次数")
    return "错误：达到最大迭代次数"


class ReactLoopNode:

    """执行当前任务，结束后同步完成达到阈值的记忆提取。"""

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

        tools = get_schemas_for_sub_agent() if is_sub_agent else get_schemas()
        tool_runner = ToolRunner(
            console=console,
            auto_confirm=auto_confirm,
            execution_context=execution_context,
        )

        try:
            result = run_react_loop(
                messages=[],
                tools=tools,
                max_iterations=MAX_ITERATIONS,
                on_tool_call=tool_runner.run,
                console=console,
                silent=False,
                session=session,
                llm_service=get_llm_service(),
                context_manager=ctx,
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

        if not is_sub_agent:
            try:
                ctx.extractor.check_and_extract(session)
            except Exception as exc:
                logger.warning(f"记忆提取未完成，保留会话供下次重试：{exc}", exc_info=True)
            finally:
                ctx.invalidate_cache()
        return result
