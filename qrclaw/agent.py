"""
agent.py —— agent 入口 + 工具函数

只负责：
- thread_local 状态管理（session、workspace、agent 深度）
- run()：对外接口，委托给 GraphRunner
- run_sub_agent()：启动子 agent
"""
import asyncio
import json
from rich.console import Console
from qrclaw.providers.base import LLMResponse
from qrclaw.memory.context.session import Session
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


# ── 全局 Extractor 注册表 ──────────────────────────────────────────────
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from qrclaw.graph.nodes.memory_extraction import MemoryExtractionNode

_global_extractor = None

def register_extractor(extractor: "MemoryExtractionNode") -> None:
    """ReactLoopNode 初始化时注册，供工具层调用"""
    global _global_extractor
    _global_extractor = extractor

def get_extractor() -> "MemoryExtractionNode | None":
    """工具层获取当前 extractor"""
    return _global_extractor


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


from qrclaw.graph.runner import GraphRunner
_runner = GraphRunner()


# ── MCP 全局状态 ──────────────────────────────────────────────────────
_mcp_manager = None


# ── 对外接口 ──────────────────────────────────────────────────────────

# ── MCP 初始化/清理辅助函数 ──────────────────────────────────────────

def _init_mcp() -> bool:
    """初始化 MCP 连接并注册工具。仅在主 agent 且 MCP 启用时执行。

    Returns:
        True 表示本次调用初始化了 MCP（调用方应在 finally 中清理）。
    """
    global _mcp_manager

    from qrclaw import config
    if not config.MCP_ENABLED or not config.MCP_SERVERS:
        return False

    try:
        from qrclaw.mcp_integration import MCPManager, register_mcp_tools

        _mcp_manager = MCPManager()

        async def _async_init():
            await _mcp_manager.connect_all(config.MCP_SERVERS)
            registered = await register_mcp_tools(_mcp_manager)
            logger.info("MCP 初始化完成，已注册 %d 个工具: %s", len(registered), registered)

        # 执行异步初始化
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, _async_init()).result(timeout=60)
            else:
                loop.run_until_complete(_async_init())
        except RuntimeError:
            asyncio.run(_async_init())

        return True
    except Exception as e:
        logger.error("MCP 初始化失败: %s", e, exc_info=True)
        _mcp_manager = None
        return False


def _cleanup_mcp():
    """安全关闭 MCP 连接并注销工具。"""
    global _mcp_manager

    if _mcp_manager is None:
        return

    try:
        from qrclaw.mcp_integration.bridge import unregister_mcp_tools

        async def _async_cleanup():
            try:
                await _mcp_manager.disconnect_all()
            except Exception as e:
                logger.warning("MCP 断开连接时出错: %s", e)
            await unregister_mcp_tools()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, _async_cleanup()).result(timeout=30)
            else:
                loop.run_until_complete(_async_cleanup())
        except RuntimeError:
            asyncio.run(_async_cleanup())

        logger.info("MCP 连接已关闭，工具已注销")
    except Exception as e:
        logger.warning("MCP 清理时出错: %s", e)
    finally:
        _mcp_manager = None



def run(
    user_input: str,
    session: Session,
    console: Console,
    workspace: Workspace,
    auto_confirm: bool = False,
):
    global _mcp_manager
    logger.info(f"收到用户输入: {user_input[:100]}...")

    set_session(session)
    set_workspace(workspace)
    session.add({"role": "user", "content": user_input})

    # 主 agent 初始化 ContextManager 单例
    from qrclaw.memory.context.context_manager import init_context_manager
    init_context_manager(session, workspace, is_sub_agent=is_sub_agent())

    # 主 agent（非子 agent）初始化 MCP 连接
    _mcp_initialized_this_run = False
    if not is_sub_agent():
        _mcp_initialized_this_run = _init_mcp()

    try:
        return _runner.run(
            user_input=user_input,
            session=session,
            console=console,
            workspace=workspace,
            auto_confirm=auto_confirm,
            is_sub_agent=is_sub_agent(),
            run_sub_agent_fn=run_sub_agent,
        )
    finally:
        # 清理：仅当本次 run 初始化了 MCP 时才关闭
        if _mcp_initialized_this_run:
            _cleanup_mcp()


def run_sub_agent(
    task: str,
    workspace: Workspace,
    agent_id: str,
    console: Console | None = None,
) -> tuple[str, Session]:
    """
    启动子 agent，返回 (结果字符串, 子session)。
    子 agent 的任务与前置步骤上下文通过 task 字符串传递。
    """
    from io import StringIO
    from rich.console import Console as RichConsole
    import uuid

    logger.info(f"启动子 agent: {agent_id}, 任务: {task[:100]}...")

    current_depth = get_agent_depth()
    set_agent_depth(current_depth + 1)
    logger.info(f"子 agent 深度: {current_depth + 1}")

    # 保存当前线程的 ContextManager，子 agent 执行完后恢复
    from qrclaw.memory.context.context_manager import get_context_manager, init_context_manager
    try:
        saved_ctx = get_context_manager()
    except RuntimeError:
        saved_ctx = None

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

    try:
        result = run(task, sub_session, sub_console, workspace, auto_confirm=True)
        result = result or "子 agent 未返回结果"
        logger.info(f"子 agent {agent_id} 执行完毕，结果长度: {len(result)} 字符")
    finally:
        set_agent_depth(current_depth)
        # 恢复主线程的 ContextManager
        if saved_ctx is not None:
            from qrclaw.memory.context.context_manager import _thread_local as _ctx_thread_local
            _ctx_thread_local.ctx = saved_ctx
            logger.debug(f"子 agent {agent_id} 执行完毕，已恢复主线程 ContextManager")

    return result, sub_session
