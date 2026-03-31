"""
agent.py —— agent 入口 + 工具函数

只负责：
- thread_local 状态管理（session、workspace、agent 深度）
- run()：对外接口，委托给 GraphRunner
- run_sub_agent()：启动子 agent
"""
import json
from rich.console import Console
from qrclaw.providers.base import LLMResponse
from qrclaw.memory.session import Session
from qrclaw.workspace import Workspace
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.agent")


# ── thread_local 状态（多线程子 agent 隔离） ──────────────────────────
import threading
_thread_local = threading.local()

def set_session(session: Session):
    _thread_local.session = session

def get_session() -> Session | None:
    return getattr(_thread_local, "session", None)

def set_workspace(workspace: Workspace):
    _thread_local.workspace = workspace

def get_workspace() -> Workspace | None:
    return getattr(_thread_local, "workspace", None)

def set_agent_depth(depth: int):
    _thread_local.agent_depth = depth

def get_agent_depth() -> int:
    return getattr(_thread_local, "agent_depth", 0)

def is_sub_agent() -> bool:
    return get_agent_depth() > 0

def get_agent_id() -> str | None:
    ws = get_workspace()
    return ws.agent_id if ws else None


# ── 工具函数 ─────────────────────────────────────────────────────────

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
            if tc.thought_signature:
                entry["__thought_signature__"] = tc.thought_signature
            tc_list.append(entry)
        msg["tool_calls"] = tc_list
    return msg


# ── 对外接口 ──────────────────────────────────────────────────────────

def run(
    user_input: str,
    session: Session,
    console: Console,
    workspace: Workspace,
    auto_confirm: bool = False,
):
    logger.info(f"收到用户输入: {user_input[:100]}...")

    set_session(session)
    set_workspace(workspace)
    session.add({"role": "user", "content": user_input})

    from qrclaw.graph.runner import GraphRunner
    runner = GraphRunner()
    return runner.run(
        user_input=user_input,
        session=session,
        console=console,
        workspace=workspace,
        auto_confirm=auto_confirm,
        is_sub_agent=is_sub_agent(),
        run_sub_agent_fn=run_sub_agent,
    )


def run_sub_agent(
    task: str,
    workspace: Workspace,
    agent_id: str,
    inherit_messages: list[dict] | None = None,
    console: Console | None = None,
) -> tuple[str, Session]:
    """
    启动子 agent，返回 (结果字符串, 子session)。

    - 串行步骤传入 inherit_messages，子 session 以主 session 的消息历史为起点
    - 并行步骤不传，各自完全独立运行
    - 串行步骤传入 console，实时输出到终端；并行步骤不传，静默运行
    """
    from io import StringIO
    from rich.console import Console as RichConsole
    import uuid

    logger.info(f"启动子 agent: {agent_id}, 任务: {task[:100]}...")

    current_depth = get_agent_depth()
    set_agent_depth(current_depth + 1)
    logger.info(f"子 agent 深度: {current_depth + 1}")

    if console is not None:
        sub_console = console
    else:
        sub_console = RichConsole(file=StringIO(), highlight=False)

    session_id = f"sub-{agent_id}-{uuid.uuid4().hex[:8]}"
    sub_session = Session(
        sessions_dir=workspace.sessions_dir,
        session_id=session_id,
        resume=False,
    )

    if inherit_messages is not None:
        # 串行：以主 session 的消息历史为起点，子 agent 能看到完整上下文
        sub_session.messages = list(inherit_messages)
        logger.info(f"子 agent {agent_id} 继承 {len(inherit_messages)} 条消息")

    try:
        result = run(task, sub_session, sub_console, workspace, auto_confirm=True)
        result = result or "子 agent 未返回结果"
        logger.info(f"子 agent {agent_id} 执行完毕，结果长度: {len(result)} 字符")
    finally:
        set_agent_depth(current_depth)

    return result, sub_session
