import json
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from qrclaw.config import MAX_ITERATIONS, COMPRESS_THRESHOLD
from qrclaw.providers import provider
from qrclaw.providers.base import LLMResponse
from qrclaw.tools.registry import get_schemas, execute, need_confirm
from qrclaw.memory.session import Session
from qrclaw.memory.step_result import StepResult
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


def _make_system_prompt(workspace: Workspace, session: Session) -> dict:
    """
    构建 system prompt。
    ReAct 循环每轮调一次，保证 active_plan 和 working_memory 始终是最新状态。
    """
    content = build_system_prompt(
        active_plan=session.active_plan,
        heartbeat_file=workspace.heartbeat_file,
        is_sub_agent=is_sub_agent(),
        agent_file=workspace.agent_file,
        skills_dir=workspace.skills_dir,
        memory_file=workspace.memory_file,
    )
    # 把工作记忆注入 system prompt，LLM 才能看到跨步骤积累的信息
    wm_prompt = session.working_memory.to_prompt()
    if wm_prompt:
        content += "\n\n" + wm_prompt
    return {"role": "system", "content": content}


def _extract_working_memory(sub_session: Session, step_id: int, output: str):
    """
    从子 session 的消息历史中自动提取工具调用信息，填充 working_memory。
    - read_file  → relevant_files
    - write_file → created_files（新文件）或 modified_files（已有文件）
    - 子 agent 最终输出 → key_findings
    """
    wm = sub_session.working_memory
    for msg in sub_session.messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except Exception:
                continue
            path = args.get("path", "")
            if not path:
                continue
            if name == "read_file":
                wm.add_relevant_file(path)
            elif name == "write_file":
                # 判断是新建还是修改：relevant_files 里有的说明之前读过，算修改
                if path in wm.relevant_files:
                    wm.add_modified_file(path)
                else:
                    wm.add_created_file(path)
    # 把子 agent 最终输出的前200字作为关键发现
    if output:
        wm.add_finding(f"Step {step_id}: {output[:200]}")


def run(user_input: str, session: Session, console: Console, workspace: Workspace, auto_confirm: bool = False):
    logger.info(f"收到用户输入: {user_input[:100]}...")

    set_session(session)
    set_workspace(workspace)
    session.add({"role": "user", "content": user_input})

    # 子 agent 跳过路由，直接走 ReAct，避免递归调用 LLM 浪费 token
    if not is_sub_agent():
        from qrclaw.graph.router_planner import route_and_plan
        result = route_and_plan(
            user_input,
            history=session.messages,
        )
        if result.route == "plan" and result.plan:
            logger.info(f"RouterPlanner 判断需要规划: {result.plan.goal}")
            return _run_with_plan(result.plan, session, console, workspace, auto_confirm)

    # 简单任务或子 agent：直接走 ReAct 循环
    return _react_loop(session, console, workspace, auto_confirm)


def run_sub_agent(task: str, workspace: Workspace, agent_id: str, inherit_working_memory=None) -> tuple[str, Session]:
    """
    以静默模式运行子 agent，返回 (结果字符串, 子session)。
    子 agent 不打印到用户终端，结果直接返回给调用方（主 agent）。
    子 agent 共享父 agent 的工作空间，不创建独立目录。

    重要：子 agent 不允许再派生子 agent，防止无限嵌套。

    Args:
        task: 子 agent 要执行的任务描述
        workspace: 父 agent 的工作空间（共享）
        agent_id: 子 agent 的 ID（用于日志标识）
        inherit_working_memory: 继承的 WorkingMemory（串行时传入，并行时为 None）
    Returns:
        tuple[str, Session]: (子 agent 的最终回复, 子 session)
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
    session_id = f"sub-{agent_id}-{uuid.uuid4().hex[:8]}"
    sub_session = Session(
        sessions_dir=workspace.sessions_dir,
        session_id=session_id,
        resume=False,
    )

    # 串行时继承父 session 的 working_memory，并行时保持空白
    if inherit_working_memory is not None:
        sub_session.working_memory = inherit_working_memory.copy()
        logger.info(f"子 agent {agent_id} 继承 working_memory: goal={inherit_working_memory.goal}")

    try:
        result = run(task, sub_session, sub_console, workspace, auto_confirm=True)
        result = result or "子 agent 未返回结果"
        logger.info(f"子 agent {agent_id} 执行完毕，结果长度: {len(result)} 字符")
    finally:
        # 恢复深度
        set_agent_depth(current_depth)

    return result, sub_session


def _run_with_plan(
    p,
    session: Session,
    console: Console,
    workspace: Workspace,
    auto_confirm: bool = False,
) -> str:
    """
    规划路径：接收已生成的 Plan → 拓扑排序执行引擎执行 → 汇总结果回 ReAct 做最终整合。

    执行流程：
      1. RouterPlanner 已生成 Plan（在 run() 中完成）
      2. Executor 拓扑分层：同层并行 spawn 子 agent，跨层串行等待
      3. 所有步骤完成后，把结果汇总注入 session，让主 ReAct 做最终回复
    """
    from qrclaw.graph.executor import execute_plan, format_results
    console.print(f"\n[bold cyan]📋 执行计划：{p.goal}[/bold cyan]")
    for step in p.steps:
        dep = f"  [dim]依赖 Step {step.depends_on}[/dim]" if step.depends_on else "  [dim]可并行[/dim]"
        console.print(f"  [yellow]Step {step.id}[/yellow] {step.description}{dep}")
    console.print()

    # 并行层结束后需要 merge 的 working_memory 队列（线程安全）
    import threading
    _pending_merge_wms = []
    _merge_lock = threading.Lock()

    def run_step(step, plan_obj) -> str:
        """
        执行单个步骤：
        - 串行步骤（单步层）：继承父 session 的 working_memory，完成后同步回去
        - 并行步骤（多步层）：不继承 working_memory，各自独立，完成后加入 merge 队列
        """
        # 判断是否是串行步骤（由 executor 在调用时通过 step._is_serial 标记）
        is_serial = getattr(step, "_is_serial", False)

        # 构建前置步骤上下文（从父 session 的 step_results 读取，修复作用域 bug）
        context = ""
        if step.depends_on:
            prior = []
            for dep_id in step.depends_on:
                dep_result = session.step_results.get(dep_id)
                if dep_result:
                    prior.append(dep_result.to_context_prompt())
            if prior:
                context = "\n\n【前置步骤结果】\n" + "\n---\n".join(prior)

        if is_serial:
            task = (
                f"【计划目标】{plan_obj.goal}\n"
                f"【当前步骤】Step {step.id}: {step.description}"
                f"{context}\n\n"
                f"【要求】只完成当前步骤，完成后返回结果摘要。"
            )
        else:
            task = (
                f"{step.description}"
                f"{context}\n\n"
                f"【要求】完成后返回结果摘要。"
            )

        # 串行步骤继承 working_memory，并行步骤不继承
        inherit_wm = session.working_memory if is_serial else None
        result, sub_session = run_sub_agent(task, workspace, f"step-{step.id}", inherit_working_memory=inherit_wm)

        # 自动从子 session 消息历史提取工具调用，填充 working_memory
        _extract_working_memory(sub_session, step.id, result)

        # 保存 StepResult 到父 session
        step_result = StepResult(
            step_id=step.id,
            description=step.description,
            status="success",
            output=result,
            summary=result[:300] + "..." if len(result) > 300 else result,
            messages=sub_session.messages,
        )
        session.step_results[step.id] = step_result

        if is_serial:
            # 串行步骤：把子 session 的 working_memory 同步回父 session
            session.working_memory = sub_session.working_memory
            logger.info(f"Step {step.id} 串行执行完毕，同步 working_memory 到父 session")
        else:
            # 并行步骤：把子 session 的 working_memory 加入 merge 队列
            with _merge_lock:
                _pending_merge_wms.append(sub_session.working_memory)
            logger.info(f"Step {step.id} 并行执行完毕，working_memory 加入 merge 队列")

        return result

    # Executor：拓扑排序执行
    results = execute_plan(p, console, run_step)

    # 并行步骤结束后，merge 所有子 session 的 working_memory 到父 session
    if _pending_merge_wms:
        for wm in _pending_merge_wms:
            session.working_memory.merge(wm)
        logger.info(f"合并 {len(_pending_merge_wms)} 个并行步骤的 working_memory")

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
    每轮重新构建 system prompt，保证 active_plan 始终反映最新状态。
    """

    permission_denied_count = 0
    MAX_PERMISSION_DENIED = 2

    for iteration in range(MAX_ITERATIONS):
        logger.debug(f"ReAct 第 {iteration + 1} 轮")
        # 每轮重建，active_plan 随 complete_step 更新后能立即反映在 prompt 里
        system_prompt = _make_system_prompt(workspace, session)
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