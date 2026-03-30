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

def get_agent_id() -> str | None:
    """获取当前线程的 agent ID"""
    ws = get_workspace()
    return ws.agent_id if ws else None


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

    # 子 agent 跳过路由，直接走 ReAct，避免递归调用 LLM 浪费 token
    if not is_sub_agent():
        from qrclaw.graph.router import route
        route_result = route(user_input, history=session.messages)
        if route_result.route == "plan":
            logger.info(f"Router 判断需要规划，进入 Planner 节点，原因: {route_result.reason}")
            return _run_with_plan(user_input, session, console, workspace, auto_confirm)

    # 简单任务或子 agent：直接走 ReAct 循环
    return _react_loop(session, console, workspace, auto_confirm)


def run_sub_agent(task: str, workspace: Workspace, agent_id: str) -> str:
    """
    以静默模式运行子 agent，返回结果字符串。
    子 agent 不打印到用户终端，结果直接返回给调用方（主 agent）。
    子 agent 共享父 agent 的工作空间，不创建独立目录。

    重要：子 agent 不允许再派生子 agent，防止无限嵌套。

    Args:
        task: 子 agent 要执行的任务描述
        workspace: 父 agent 的工作空间（共享）
        agent_id: 子 agent 的 ID（用于日志标识）
    Returns:
        str: 子 agent 的最终回复
    """
    from io import StringIO
    from rich.console import Console as RichConsole
    from qrclaw.memory.session import Session
    import uuid

    logger.info(f"启动子 agent: {agent_id}, 任务: {task[:100]}...")

    # 设置子 agent 深度（当前深度 + 1）
    current_depth = get_agent_depth()
    set_agent_depth(current_depth + 1)
    logger.info(f"子 agent 深度: {current_depth + 1}")

    buffer = StringIO()
    sub_console = RichConsole(file=buffer, highlight=False)

    # 子 agent 使用独立的 session 文件（但共享工作空间）
    # 使用 uuid 区分不同子 agent 的 session
    session_id = f"sub-{agent_id}-{uuid.uuid4().hex[:8]}"
    sub_session = Session(
        sessions_dir=workspace.sessions_dir,
        session_id=session_id,
        resume=False,
    )

    try:
        result = run(task, sub_session, sub_console, workspace, auto_confirm=True)
        result = result or "子 agent 未返回结果"
        logger.info(f"子 agent {agent_id} 执行完毕，结果长度: {len(result)} 字符")
    finally:
        # 恢复深度
        set_agent_depth(current_depth)

    return result


def _run_with_plan(
    user_input: str,
    session: Session,
    console: Console,
    workspace: Workspace,
    auto_confirm: bool = False,
) -> str:
    """
    规划路径：Planner 生成 Plan → 拓扑排序执行引擎执行 → 汇总结果回 ReAct 做最终整合。

    执行流程：
      1. Planner 生成带 depends_on 的 Plan
      2. Executor 拓扑分层：同层并行 spawn 子 agent，跨层串行等待
      3. 所有步骤完成后，把结果汇总注入 session，让主 ReAct 做最终回复
    """
    from qrclaw.graph.planner import plan as make_plan
    from qrclaw.graph.executor import execute_plan, format_results

    # Planner：生成执行计划
    p = make_plan(user_input)
    console.print(f"\n[bold cyan]📋 执行计划：{p.goal}[/bold cyan]")
    for step in p.steps:
        dep = f"  [dim]依赖 Step {step.depends_on}[/dim]" if step.depends_on else "  [dim]可并行[/dim]"
        console.print(f"  [yellow]Step {step.id}[/yellow] {step.description}{dep}")
    console.print()

    # 定义单步执行函数：每个步骤作为子 agent 跑一次 ReAct 循环
    def run_step(step, plan_obj) -> str:
        # 把前置步骤的结果拼入 task，解决子 agent 间上下文断裂问题
        context = ""
        if step.depends_on:
            prior = []
            for dep_id in step.depends_on:
                dep_result = results.get(dep_id, "")
                if dep_result:
                    # 结果太长截断，避免 token 爆炸
                    snippet = dep_result[:800] + "\n...(已截断)" if len(dep_result) > 800 else dep_result
                    prior.append(f"Step {dep_id} 结果：\n{snippet}")
            if prior:
                context = "\n\n【前置步骤结果】\n" + "\n---\n".join(prior)

        task = (
            f"【计划目标】{plan_obj.goal}\n"
            f"【当前步骤】Step {step.id}: {step.description}"
            f"{context}\n\n"
            f"【要求】只完成当前步骤，完成后返回结果摘要。"
        )
        return run_sub_agent(task, workspace, f"step-{step.id}")

    # Executor：拓扑排序执行
    results = execute_plan(p, console, run_step)

    # 把所有步骤结果汇总注入 session，让主 ReAct 做最终整合回复
    summary = format_results(p, results)
    session.add({
        "role": "user",
        "content": (
            f"以上是任务「{p.goal}」的各步骤执行结果，"
            f"请根据这些结果给出最终的整合回复。\n\n{summary}"
        ),
    })

    # 走一次 ReAct 做最终整合（此时 session 里已有所有步骤结果）
    logger.info("所有步骤执行完毕，进入最终整合 ReAct")
    return _react_loop(session, console, workspace, auto_confirm)


def _react_loop(
    session: Session,
    console: Console,
    workspace: Workspace,
    auto_confirm: bool = False,
) -> str:
    """
    纯 ReAct 循环，不做路由判断。
    供 run() 直接路径和 _run_with_plan() 最终整合复用。
    """
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
            is_sub_agent=is_sub_agent(),
            agent_file=workspace.agent_file,
        )
    }

    permission_denied_count = 0
    MAX_PERMISSION_DENIED = 2

    for iteration in range(MAX_ITERATIONS):
        logger.debug(f"ReAct 第 {iteration + 1} 轮")
        messages = [system_prompt, *session.messages]

        with console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots"):
            try:
                response = provider.chat(messages, tools=get_schemas())
                session.update_tokens(
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    total_tokens=response.total_tokens,
                )
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}", exc_info=True)
                raise

        if response.prompt_tokens > COMPRESS_THRESHOLD:
            compressor.summarize(session)

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

            if permission_denied_count >= MAX_PERMISSION_DENIED:
                break

            if name in ("create_plan", "complete_step"):
                show_plan_progress(console, session)

    logger.warning("达到最大迭代次数")
    return "错误：达到最大迭代次数"