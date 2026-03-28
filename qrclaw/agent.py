import json
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from qrclaw.config import MAX_ITERATIONS, COMPRESS_THRESHOLD
from qrclaw.providers import provider
from qrclaw.providers.base import LLMResponse
from qrclaw.tools.registry import get_schemas, execute, need_confirm
from qrclaw.memory.session import Session
from qrclaw.memory import compressor, LongTermMemory
from qrclaw.prompt import build_system_prompt
from qrclaw.cli.display import show_plan_progress
from qrclaw.workspace import Workspace
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.agent")


# 用 threading.local 隔离每个线程的 session/workspace
# 多个子 agent 并行时，各自的上下文互不干扰
import threading
_thread_local = threading.local()

def set_session(session: Session):
    """注入当前线程的 session"""
    _thread_local.session = session

def get_session() -> Session | None:
    """获取当前线程的 session"""
    return getattr(_thread_local, "session", None)

def set_workspace(workspace: Workspace):
    """注入当前线程的 workspace"""
    _thread_local.workspace = workspace

def get_workspace() -> Workspace | None:
    """获取当前线程的 workspace"""
    return getattr(_thread_local, "workspace", None)

def set_agent_depth(depth: int):
    """设置当前 agent 的深度（0=顶层 agent）"""
    _thread_local.agent_depth = depth

def get_agent_depth() -> int:
    """获取当前 agent 的深度，默认为 0（顶层）"""
    return getattr(_thread_local, "agent_depth", 0)

def is_sub_agent() -> bool:
    """判断当前是否是子 agent"""
    return get_agent_depth() > 0


def _dump_assistant_msg(response: LLMResponse) -> dict:
    """把 LLMResponse 转成可存入 session 的 assistant 消息 dict"""
    msg: dict = {"role": "assistant", "content": response.content or ""}
    if response.tool_calls:
        tc_list = []
        for tc in response.tool_calls:
            entry = {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            # 保留 thought_signature，回传给 Vertex AI 时需要
            if tc.thought_signature:
                entry["__thought_signature__"] = tc.thought_signature
            tc_list.append(entry)
        msg["tool_calls"] = tc_list
    return msg


def run(user_input: str, session: Session, console: Console, workspace: Workspace, auto_confirm: bool = False):
    logger.info(f"收到用户输入: {user_input[:100]}...")

    set_session(session)
    set_workspace(workspace)
    session.add({"role": "user", "content": user_input})

    # system prompt 每次实时构建，不存进 session
    # 这样工作目录、工具列表永远是最新的
    from qrclaw.skills.registry import SkillRegistry
    tool_names = [s["function"]["name"] for s in get_schemas()]
    memory = LongTermMemory(workspace.memory_file)
    skill_registry = SkillRegistry()
    skill_registry.load_from_dir(workspace.skills_dir)
    system_prompt = {
        "role": "system",
        "content": build_system_prompt(
            tool_names,
            memory,
            skill_registry,
            active_plan=session.active_plan,
            heartbeat_file=workspace.heartbeat_file,
        )
    }
    logger.debug(f"System prompt 已构建，可用工具: {', '.join(tool_names)}")

    # 权限拒绝计数器
    permission_denied_count = 0
    MAX_PERMISSION_DENIED = 2

    for iteration in range(MAX_ITERATIONS):
        logger.debug(f"开始第 {iteration + 1} 轮推理")

        # 每次调 LLM 时把 system prompt 拼到最前面
        messages = [system_prompt, *session.messages]

        # spinner 只包住 LLM 请求这一步，拿到响应立即退出
        with console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots"):
            logger.debug(f"调用 LLM，消息数: {len(messages)}")
            try:
                response = provider.chat(messages, tools=get_schemas())
                session.update_tokens(
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    total_tokens=response.total_tokens,
                )
                logger.info(f"LLM 响应成功，使用 {response.total_tokens} tokens")
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}", exc_info=True)
                raise

        finish_reason = response.finish_reason

        # 检查是否需要压缩
        if response.prompt_tokens > COMPRESS_THRESHOLD:
            logger.warning(f"Prompt tokens ({response.prompt_tokens}) 超过阈值 ({COMPRESS_THRESHOLD})，触发压缩")
            compressor.summarize(session)
        else:
            logger.debug(f"Prompt tokens: {response.prompt_tokens}, 阈值: {COMPRESS_THRESHOLD}")

        if finish_reason == "stop":
            logger.info(f"推理完成，最终答案长度: {len(response.content)} 字符")
            session.add({"role": "assistant", "content": response.content})
            console.print()  # 添加空行
            console.print(Panel(
                Markdown(response.content, code_theme="ansi_dark"),
                title="[bold green]Agent[/bold green]",
                border_style="green",
                expand=True,
            ))
            console.print()  # 添加空行
            return response.content

        if finish_reason == "length":
            logger.warning("LLM 响应被截断 (finish_reason=length)")
            console.print("[bold red]警告：回复被截断，请尝试简化任务[/bold red]\n")
            return "错误：回复被截断"

        # assistant 消息只存一次，在工具循环之前
        assistant_msg_saved = False

        for tc in response.tool_calls:
            name = tc.name
            arguments = tc.arguments
            logger.info(f"工具调用: {name}, 参数: {arguments[:200]}...")

            # 把 JSON 字符串格式化后高亮显示
            try:
                args_formatted = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
            except Exception:
                args_formatted = arguments
            console.print()  # 添加空行
            console.print(Panel(
                f"[bold cyan]{name}[/bold cyan]\n" + args_formatted,
                title="[bold yellow]▶ 调用工具[/bold yellow]",
                border_style="yellow",
                expand=False,
            ))
            console.print()  # 添加空行

            # 高风险工具执行前，询问用户确认（子 agent 自动跳过）
            if need_confirm(name) and not auto_confirm:
                logger.debug(f"工具 {name} 需要用户确认")
                console.print(f"[bold red]⚠ 需要确认[/bold red] 是否允许执行？(y/n) ", end="")
                choice = input().strip().lower()
                if choice != "y":
                    result = "用户拒绝执行此操作"
                    logger.warning(f"用户拒绝执行工具: {name}")
                    console.print(Panel(result, title="[bold red]已拒绝[/bold red]", border_style="red", expand=False))
                    if not assistant_msg_saved:
                        session.add(_dump_assistant_msg(response))
                        assistant_msg_saved = True
                    session.add({"role": "tool", "tool_call_id": tc.id, "content": result})
                    break  # 退出工具循环，回到外层让LLM重新推理

            if not assistant_msg_saved:
                session.add(_dump_assistant_msg(response))
                assistant_msg_saved = True

            try:
                result = execute(name, arguments)
                logger.info(f"工具执行成功: {name}, 结果长度: {len(result)} 字符")
                # 工具执行成功，重置权限拒绝计数器
                permission_denied_count = 0
            except PermissionError as e:
                # 权限拒绝
                permission_denied_count += 1
                result = str(e)
                logger.warning(f"权限拒绝 ({permission_denied_count}/{MAX_PERMISSION_DENIED}): {name}, 原因: {e}")
                
                # 检查是否达到上限
                if permission_denied_count >= MAX_PERMISSION_DENIED:
                    console.print(Panel(
                        f"[bold red]您没有权限执行这个操作！[/bold red]\n\n"
                        f"连续 {MAX_PERMISSION_DENIED} 次权限拒绝，任务终止。\n\n"
                        f"最后一次拒绝原因:\n{e}",
                        title="[bold red]⛔ 权限不足[/bold red]",
                        border_style="red",
                        expand=False,
                    ))
                    console.print()
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
            console.print()  # 添加空行

            session.add({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

            # 如果已经达到权限拒绝上限，直接退出
            if permission_denied_count >= MAX_PERMISSION_DENIED:
                break

            if name in ("create_plan", "complete_step"):
                show_plan_progress(console, session)

    logger.warning("达到最大迭代次数，强制退出")
    return "错误：达到最大迭代次数"


def run_sub_agent(task: str, sub_workspace: Workspace) -> str:
    """
    以静默模式运行子 agent，返回结果字符串。
    子 agent 不打印到用户终端，结果直接返回给调用方（主 agent）。
    任务完成后自动清理工作空间（保留 logs，删除 sessions/skills/MEMORY.md）。

    重要：子 agent 不允许再派生子 agent，防止无限嵌套。

    Args:
        task: 子 agent 要执行的任务描述
        sub_workspace: 子 agent 的工作空间
    Returns:
        str: 子 agent 的最终回复
    """
    import shutil
    from io import StringIO
    from rich.console import Console as RichConsole
    from qrclaw.memory.session import Session

    logger.info(f"启动子 agent: {sub_workspace.agent_id}, 任务: {task[:100]}...")

    # 设置子 agent 深度（当前深度 + 1）
    current_depth = get_agent_depth()
    set_agent_depth(current_depth + 1)
    logger.info(f"子 agent 深度: {current_depth + 1}")

    buffer = StringIO()
    sub_console = RichConsole(file=buffer, highlight=False)
    sub_session = Session(sessions_dir=sub_workspace.sessions_dir, resume=False)

    try:
        result = run(task, sub_session, sub_console, sub_workspace, auto_confirm=True)
        result = result or "子 agent 未返回结果"
        logger.info(f"子 agent {sub_workspace.agent_id} 执行完毕，结果长度: {len(result)} 字符")
    finally:
        # 恢复深度
        set_agent_depth(current_depth)

    # 清理工作空间：保留 logs，删除 sessions/skills/MEMORY.md
    try:
        if sub_workspace.sessions_dir.exists():
            shutil.rmtree(sub_workspace.sessions_dir)
        if sub_workspace.skills_dir.exists():
            shutil.rmtree(sub_workspace.skills_dir)
        if sub_workspace.memory_file.exists():
            sub_workspace.memory_file.unlink()
        logger.info(f"子 agent {sub_workspace.agent_id} 工作空间已清理（logs 保留）")
    except Exception as e:
        logger.warning(f"清理子 agent 工作空间失败: {e}")

    return result