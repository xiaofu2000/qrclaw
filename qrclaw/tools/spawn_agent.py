"""
spawn_agent 工具

允许主 agent 并行创建多个子 agent 执行任务。
子 agent 在后台线程运行，立即返回，不阻塞主 agent。

工作空间：
- 子 agent 共享父 agent 的工作空间（子 agent 是一次性的）
- 不需要创建独立目录，也不需要清理

沙箱支持：
- 子 agent 根据配置自动创建沙箱
- 子 agent 完成后自动销毁沙箱
- 配置从 permissions.yaml 读取

重要：子 agent 不允许再派生子 agent，防止无限嵌套。
"""
from pathlib import Path
import threading
import time
from rich.console import Console
from rich.panel import Panel
from pydantic import BaseModel, Field
from qrclaw.cancellation import (
    TaskCancelledError,
    bind_cancel_token,
    check_cancelled,
    get_cancel_token,
    register_sub_agent_token,
    unregister_sub_agent_token,
)
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.spawn_agent")

# 全局任务池
_task_pool: dict = {}
_task_pool_lock = threading.Lock()

# 全局 console
_console: Console = None


def set_console(console: Console):
    """注入全局 console"""
    global _console
    _console = console


def get_task_pool() -> dict:
    """获取任务池"""
    return _task_pool


def get_task_pool_lock() -> threading.Lock:
    """获取任务池锁"""
    return _task_pool_lock


def cancel_tasks_for_session(sessions_dir: Path, parent_id: str, join_timeout: float = 0.5) -> bool:
    """取消指定父会话所有正在运行的后台子任务，并清理残留运行状态。"""
    from qrclaw.subagent_progress import record_sub_agent_end, sub_agent_snapshot

    if not parent_id:
        return False
    workspace_key = str(Path(sessions_dir).resolve())
    running_items: list[tuple[tuple[str, str, str], dict]] = []
    with _task_pool_lock:
        for key, item in list(_task_pool.items()):
            if key[0] == workspace_key and key[1] == parent_id and item.get("status") == "running":
                running_items.append((key, item))

    if not running_items:
        return False

    for _, item in running_items:
        token = item.get("token")
        if token is not None:
            token.cancel()

    deadline = time.monotonic() + max(0.0, join_timeout)
    for _, item in running_items:
        thread = item.get("thread")
        if thread is not None and thread.is_alive():
            remaining = deadline - time.monotonic()
            if remaining > 0:
                thread.join(timeout=remaining)

    for key, item in running_items:
        child_id = item.get("child_id")
        if child_id and sub_agent_snapshot(child_id, sessions_dir) is not None:
            record_sub_agent_end(sessions_dir, parent_id, child_id, "interrupted")
        with _task_pool_lock:
            current = _task_pool.get(key)
            if current is item and current.get("status") == "running":
                current["status"] = "cancelled"
                current["result"] = "任务已由用户停止"

    return True


class SpawnAgentArgs(BaseModel):
    agent_id: str = Field(description="子 agent 的 ID，例如 'coder'、'reviewer'")
    task: str = Field(description="交给子 agent 的任务描述，要清晰具体")


@register(
    description="在后台启动子 agent 并行执行任务，立即返回不阻塞。任务可拆分时批量调用此工具启动多个子 agent，再用 wait_agents 统一等待结果。子 agent 完成时结果自动打印。",
    args_model=SpawnAgentArgs,
)
def spawn_agent(agent_id: str, task: str) -> str:
    """在后台线程启动子 agent"""
    check_cancelled()
    from qrclaw.agent import get_session, get_workspace, run_sub_agent, is_sub_agent
    from qrclaw.subagent_progress import prepare_sub_agent, record_sub_agent_end, sub_agent_snapshot
    from qrclaw.workspace import Workspace, is_valid_agent_id
    from qrclaw.sandbox import is_sandbox_enabled

    # 子 agent 不允许再派生子 agent
    if is_sub_agent():
        return "错误：子 agent 不允许再派生子 agent，这会导致无限嵌套。请直接执行任务，不要使用 spawn_agent。"

    if not is_valid_agent_id(agent_id):
        return f"错误：无效的子 Agent ID: {agent_id}"

    parent_session = get_session()
    parent_id = parent_session.session_id if parent_session else ""
    parent_workspace = get_workspace() or Workspace("default")
    pool_key = (str(parent_workspace.sessions_dir.resolve()), parent_id, agent_id)

    # 同一父会话中的同名子任务不能同时运行。
    with _task_pool_lock:
        if pool_key in _task_pool and _task_pool[pool_key]["status"] == "running":
            return f"子 agent '{agent_id}' 已在运行中，请等待完成或使用不同的 agent_id"

    # 获取父 agent 的工作空间（子 agent 共享）
    # 检查是否需要创建沙箱
    policy_agent_id = parent_workspace.sandbox_policy_agent_id if is_sandbox_enabled(parent_workspace.sandbox_policy_agent_id) else agent_id
    sandbox_enabled = is_sandbox_enabled(policy_agent_id)
    
    logger.info(f"启动子 agent: {agent_id}, 共享工作空间: {parent_workspace.root}, 沙箱: {sandbox_enabled}")
    child_id = prepare_sub_agent(parent_session, parent_workspace, agent_id, task) if parent_session else None
    sub_token, owner_token = register_sub_agent_token(
        parent_workspace.sessions_dir,
        parent_id,
        get_cancel_token(),
    )

    def _run():
        try:
            with bind_cancel_token(sub_token):
                check_cancelled()
                # 执行子 agent（共享父 agent 的工作空间）
                kwargs = {"sandbox_policy_agent_id": policy_agent_id}
                if child_id:
                    kwargs.update(child_session_id=child_id, parent_session_id=parent_id)
                result, _ = run_sub_agent(task, parent_workspace, agent_id, **kwargs)
                check_cancelled()
            if child_id and sub_agent_snapshot(child_id, parent_workspace.sessions_dir):
                record_sub_agent_end(parent_workspace.sessions_dir, parent_id, child_id, "completed")
            
            with _task_pool_lock:
                if pool_key in _task_pool:
                    _task_pool[pool_key]["status"] = "done"
                    _task_pool[pool_key]["result"] = result
            
            logger.info(f"子 agent {agent_id} 完成")
            
            if _console:
                _console.print()
                _console.print(Panel(
                    result,
                    title=f"[bold green]子 agent '{agent_id}' 完成[/bold green]",
                    border_style="green",
                    expand=False,
                ))
                _console.print()

        except TaskCancelledError:
            logger.info(f"子 agent {agent_id} 已停止")
            if child_id and sub_agent_snapshot(child_id, parent_workspace.sessions_dir):
                record_sub_agent_end(parent_workspace.sessions_dir, parent_id, child_id, "interrupted")
            with _task_pool_lock:
                if pool_key in _task_pool:
                    _task_pool[pool_key]["status"] = "cancelled"
                    _task_pool[pool_key]["result"] = "任务已由用户停止"

        except Exception as e:
            logger.error(f"子 agent {agent_id} 出错: {e}", exc_info=True)
            if child_id and sub_agent_snapshot(child_id, parent_workspace.sessions_dir):
                record_sub_agent_end(parent_workspace.sessions_dir, parent_id, child_id, "failed")
            with _task_pool_lock:
                if pool_key in _task_pool:
                    _task_pool[pool_key]["status"] = "error"
                    _task_pool[pool_key]["result"] = f"执行出错: {e}"
            if _console:
                _console.print(f"\n[bold red]子 agent '{agent_id}' 执行出错: {e}[/bold red]\n")
        finally:
            unregister_sub_agent_token(parent_workspace.sessions_dir, parent_id, sub_token, owner_token)
            
    thread = threading.Thread(target=_run, name=f"sub-agent-{agent_id}", daemon=True)

    with _task_pool_lock:
        _task_pool[pool_key] = {
            "status": "running",
            "result": None,
            "thread": thread,
            "sandbox_enabled": sandbox_enabled,
            "token": sub_token,
            "child_id": child_id,
        }

    try:
        thread.start()
    except Exception:
        unregister_sub_agent_token(parent_workspace.sessions_dir, parent_id, sub_token, owner_token)
        with _task_pool_lock:
            _task_pool.pop(pool_key, None)
        if child_id:
            record_sub_agent_end(parent_workspace.sessions_dir, parent_id, child_id, "failed")
        raise
    
    sandbox_hint = " (沙箱已启用)" if sandbox_enabled else ""
    return f"子 agent '{agent_id}' 已在后台启动{sandbox_hint}，任务：{task[:50]}{'...' if len(task) > 50 else ''}"
