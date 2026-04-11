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
from qrclaw.config import MAX_ITERATIONS, COMPRESS_THRESHOLD
from qrclaw.providers import provider
from qrclaw.providers.base import LLMResponse
from qrclaw.tools.registry import get_schemas, get_schemas_for_sub_agent, execute, need_confirm
from qrclaw.memory.context.session import Session
from qrclaw.memory.context.context_manager import get_context_manager
from qrclaw.workspace import Workspace
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.react_loop")


def _dump_assistant_msg(response: LLMResponse) -> dict:
    msg: dict = {"role": "assistant", "content": response.content or ""}
    if response.tool_calls:
        tc_list = []
        for tc in response.tool_calls:
            entry = {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            if tc.thought_signature:
                entry["__thought_signature__"] = tc.thought_signature
            tc_list.append(entry)
        msg["tool_calls"] = tc_list
    return msg


def run_react_loop(
    messages: list[dict],
    tools: list[dict],
    max_iterations: int = MAX_ITERATIONS,
    on_tool_call: Optional[Callable[[str, str], str]] = None,
    on_finish: Optional[Callable[[str], None]] = None,
    console: Optional[Console] = None,
    silent: bool = False,
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
        console: Rich Console，silent=True 时不输出
        silent: 静默模式，不打印任何内容

    Returns:
        最终 LLM 输出内容
    """
    _console = console or Console()

    for iteration in range(max_iterations):
        logger.debug(f"run_react_loop 第 {iteration + 1} 轮")

        with _console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots") if not silent else _noop_ctx():
            try:
                response = provider.chat(messages, tools=tools)
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}", exc_info=True)
                raise

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
            messages.append({"role": "assistant", "content": response.content or ""})
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
                messages.append(_dump_assistant_msg(response))
                assistant_msg_saved = True

            try:
                if on_tool_call:
                    result = on_tool_call(name, arguments)
                else:
                    result = execute(name, arguments)
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

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

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
                register_extractor,
            )
            from qrclaw.memory.wiki import WikiMemory

            if workspace:
                memory = WikiMemory(memory_dir=workspace.memory_dir)
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
    ) -> str:
        ctx = get_context_manager()
        permission_denied_count = 0
        MAX_PERMISSION_DENIED = 2

        # 仅主 agent 启用记忆提取
        memory_integration = self._get_memory_integration(workspace) if not is_sub_agent else None

        tools = get_schemas_for_sub_agent() if is_sub_agent else get_schemas()

        def _tool_call(name: str, arguments: str) -> str:
            nonlocal permission_denied_count
            if need_confirm(name) and not auto_confirm:
                console.print(f"[bold red]⚠ 需要确认[/bold red] 是否允许执行？(y/n) ", end="")
                choice = input().strip().lower()
                if choice != "y":
                    return "用户拒绝执行此操作"
            try:
                result = execute(name, arguments)
                permission_denied_count = 0
                return result
            except PermissionError as e:
                permission_denied_count += 1
                if permission_denied_count >= MAX_PERMISSION_DENIED:
                    raise RuntimeError(f"连续 {MAX_PERMISSION_DENIED} 次权限拒绝，任务终止") from e
                return str(e)
            except Exception as e:
                logger.error(f"工具执行失败: {name}, 错误: {e}", exc_info=True)
                return f"工具执行失败: {str(e)}"

        def _on_finish(content: str) -> None:
            session.add({"role": "assistant", "content": content})
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
                on_tool_call=_tool_call,
                on_finish=_on_finish,
                console=console,
                silent=False,
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
