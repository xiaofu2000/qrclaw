"""
ReactLoop 节点

职责：主 agent 的 ReAct 循环，工具调用 + LLM 推理直到得出最终答案。
适用于：简单任务直接执行、并行计划完成后的最终整合。
"""
import json
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from qrclaw.config import MAX_ITERATIONS, COMPRESS_THRESHOLD
from qrclaw.providers import provider
from qrclaw.providers.base import LLMResponse
from qrclaw.tools.registry import get_schemas, get_schemas_for_sub_agent, execute, need_confirm
from qrclaw.memory.session import Session
from qrclaw.memory.context_manager import get_context_manager
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


class ReactLoopNode:

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

        for iteration in range(MAX_ITERATIONS):
            logger.debug(f"ReAct 第 {iteration + 1} 轮")

            # 调用前检查 token 数，超限提前压缩
            ctx.compress_if_needed()
            messages = ctx.build_messages("react")

            tools = get_schemas_for_sub_agent() if is_sub_agent else get_schemas()

            with console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots"):
                try:
                    response = provider.chat(messages, tools=tools)
                    session.update_tokens(
                        prompt_tokens=response.prompt_tokens,
                        completion_tokens=response.completion_tokens,
                        total_tokens=response.total_tokens,
                    )
                except Exception as e:
                    logger.error(f"LLM 调用失败: {e}", exc_info=True)
                    raise

            if response.prompt_tokens > COMPRESS_THRESHOLD:
                ctx.compress_if_needed()

            if response.finish_reason == "stop":
                session.add({"role": "assistant", "content": response.content})
                console.print()
                console.print(Panel(
                    Markdown(response.content, code_theme="ansi_dark"),
                    title="[bold green]Agent[/bold green]",
                    border_style="green",
                    expand=True,
                ))
                console.print()
                return response.content

            if response.finish_reason == "length":
                console.print("[bold red]警告：回复被截断，请尝试简化任务[/bold red]\n")
                return "错误：回复被截断"

            assistant_msg_saved = False

            for tc in response.tool_calls:
                name = tc.name
                arguments = tc.arguments
                logger.info(f"工具调用: {name}")

                try:
                    args_formatted = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
                except Exception:
                    args_formatted = arguments

                console.print()
                console.print(Panel(
                    f"[bold cyan]{name}[/bold cyan]\n" + args_formatted,
                    title="[bold yellow]▶ 调用工具[/bold yellow]",
                    border_style="yellow",
                    expand=False,
                ))
                console.print()

                if need_confirm(name) and not auto_confirm:
                    console.print(f"[bold red]⚠ 需要确认[/bold red] 是否允许执行？(y/n) ", end="")
                    choice = input().strip().lower()
                    if choice != "y":
                        result = "用户拒绝执行此操作"
                        console.print(Panel(result, title="[bold red]已拒绝[/bold red]", border_style="red", expand=False))
                        if not assistant_msg_saved:
                            session.add(_dump_assistant_msg(response))
                            assistant_msg_saved = True
                        session.add({"role": "tool", "tool_call_id": tc.id, "content": result})
                        break

                if not assistant_msg_saved:
                    session.add(_dump_assistant_msg(response))
                    assistant_msg_saved = True

                try:
                    result = execute(name, arguments)
                    permission_denied_count = 0
                except PermissionError as e:
                    permission_denied_count += 1
                    result = str(e)
                    if permission_denied_count >= MAX_PERMISSION_DENIED:
                        console.print(Panel(
                            f"[bold red]连续 {MAX_PERMISSION_DENIED} 次权限拒绝，任务终止。[/bold red]\n\n{e}",
                            title="[bold red]⛔ 权限不足[/bold red]",
                            border_style="red",
                            expand=False,
                        ))
                        return f"错误：连续 {MAX_PERMISSION_DENIED} 次权限拒绝，任务终止"
                except Exception as e:
                    result = f"工具执行失败: {str(e)}"
                    logger.error(f"工具执行失败: {name}, 错误: {e}", exc_info=True)

                preview = result[:200] + "..." if len(result) > 200 else result
                console.print(Panel(
                    preview,
                    title="[bold blue]◀ 工具结果[/bold blue]",
                    border_style="blue",
                    expand=False,
                ))
                console.print()

                session.add({"role": "tool", "tool_call_id": tc.id, "content": result})

        logger.warning("达到最大迭代次数")
        return "错误：达到最大迭代次数"
