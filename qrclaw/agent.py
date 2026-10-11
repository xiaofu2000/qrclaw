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


# ── 当前线程的 Extractor ──────────────────────────────────────────────
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from qrclaw.graph.nodes.memory_extraction import MemoryExtractionNode

def register_extractor(extractor: "MemoryExtractionNode") -> None:
    """注册当前线程的记忆节点，供工具层调用。"""
    _thread_local.extractor = extractor

def get_extractor() -> "MemoryExtractionNode | None":
    """工具层获取当前 extractor"""
    return getattr(_thread_local, "extractor", None)


# ── 工具函数 ─────────────────────────────────────────────────────────

def _dump_assistant_msg(response: LLMResponse) -> dict:
    """把 LLMResponse 转成可存入 session 的 assistant 消息 dict"""
    msg: dict = {"role": "assistant", "content": response.content or ""}
    if response.reasoning_content:
        msg["reasoning_content"] = response.reasoning_content
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
_mcp_loop = None
_mcp_stop = None
_mcp_thread = None


# ── 对外接口 ──────────────────────────────────────────────────────────

# ── MCP 初始化/清理辅助函数 ──────────────────────────────────────────

def _init_mcp() -> bool:
    """初始化 MCP 连接并注册工具。仅在主 agent 且 MCP 启用时执行。

    Returns:
        True 表示本次调用初始化了 MCP（调用方应在 finally 中清理）。
    """
    global _mcp_manager, _mcp_loop, _mcp_stop, _mcp_thread

    from qrclaw import config
    if not config.MCP_ENABLED or not config.MCP_SERVERS:
        return False

    try:
        from concurrent.futures import Future
        from qrclaw.mcp_integration import MCPManager, register_mcp_tools
        from qrclaw.mcp_integration.bridge import unregister_mcp_tools

        manager = MCPManager()
        loop = asyncio.new_event_loop()
        stop = asyncio.Event()
        ready = Future()

        async def lifecycle():
            try:
                await manager.connect_all(config.MCP_SERVERS)
                registered = await register_mcp_tools(manager)
                ready.set_result(registered)
                await stop.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
                else:
                    logger.error("MCP 运行失败: %s", exc, exc_info=True)
            finally:
                await unregister_mcp_tools()
                await manager.disconnect_all()

        def worker():
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(lifecycle())
            finally:
                loop.close()

        thread = threading.Thread(target=worker, name="mcp-runtime", daemon=True)
        thread.start()
        try:
            registered = ready.result(timeout=60)
        except Exception:
            loop.call_soon_threadsafe(stop.set)
            thread.join(timeout=30)
            raise

        _mcp_manager, _mcp_loop, _mcp_stop, _mcp_thread = manager, loop, stop, thread
        logger.info("MCP 初始化完成，已注册 %d 个工具: %s", len(registered), registered)
        return True
    except Exception as e:
        logger.error("MCP 初始化失败: %s", e, exc_info=True)
        return False


def _cleanup_mcp():
    """安全关闭 MCP 连接并注销工具。"""
    global _mcp_manager, _mcp_loop, _mcp_stop, _mcp_thread

    if _mcp_thread is None:
        return

    try:
        _mcp_loop.call_soon_threadsafe(_mcp_stop.set)
        _mcp_thread.join(timeout=30)
        if _mcp_thread.is_alive():
            logger.warning("MCP 清理超时，后台线程仍在运行")
            return
        logger.info("MCP 连接已关闭，工具已注销")
    except Exception as e:
        logger.warning("MCP 清理时出错: %s", e)
    finally:
        if not _mcp_thread.is_alive():
            _mcp_manager = _mcp_loop = _mcp_stop = _mcp_thread = None



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
    session.start_run_usage()
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

    sub_workspace = Workspace(agent_id=agent_id, _root=workspace.root)
    saved_session = get_session()
    saved_workspace = get_workspace()

    logger.info(f"启动子 agent: {agent_id}, 任务: {task[:100]}...")

    current_depth = get_agent_depth()
    set_agent_depth(current_depth + 1)
    logger.info(f"子 agent 深度: {current_depth + 1}")

    # 保存当前线程的 ContextManager，子 agent 执行完后恢复
    from qrclaw.memory.context.runtime.manager import get_context_manager, _thread_local as _ctx_thread_local
    try:
        saved_ctx = get_context_manager()
    except RuntimeError:
        saved_ctx = None

    try:
        sub_console = console or RichConsole(file=StringIO(), highlight=False)
        session_id = f"sub-{agent_id}-{uuid.uuid4().hex[:8]}"
        sub_session = Session(
            sessions_dir=sub_workspace.sessions_dir,
            session_id=session_id,
            resume=False,
        )
        result = run(task, sub_session, sub_console, sub_workspace, auto_confirm=True)
        result = result or "子 agent 未返回结果"
        logger.info(f"子 agent {agent_id} 执行完毕，结果长度: {len(result)} 字符")
    finally:
        set_agent_depth(current_depth)
        set_session(saved_session)
        set_workspace(saved_workspace)
        _ctx_thread_local.ctx = saved_ctx

    return result, sub_session
