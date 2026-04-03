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
import os
import threading
from rich.console import Console
from rich.panel import Panel
from pydantic import BaseModel, Field
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


class SpawnAgentArgs(BaseModel):
    agent_id: str = Field(description="子 agent 的 ID，例如 'coder'、'reviewer'")
    task: str = Field(description="交给子 agent 的任务描述，要清晰具体")


@register(
    description="在后台启动子 agent 并行执行任务，立即返回不阻塞。任务可拆分时批量调用此工具启动多个子 agent，再用 wait_agents 统一等待结果。子 agent 完成时结果自动打印。",
    args_model=SpawnAgentArgs,
)
def spawn_agent(agent_id: str, task: str) -> str:
    """在后台线程启动子 agent"""
    from qrclaw.agent import get_workspace, run_sub_agent, is_sub_agent
    from qrclaw.workspace import Workspace
    from qrclaw.sandbox import is_sandbox_enabled

    # 子 agent 不允许再派生子 agent
    if is_sub_agent():
        return "错误：子 agent 不允许再派生子 agent，这会导致无限嵌套。请直接执行任务，不要使用 spawn_agent。"

    # 检查是否已在运行
    with _task_pool_lock:
        if agent_id in _task_pool and _task_pool[agent_id]["status"] == "running":
            return f"子 agent '{agent_id}' 已在运行中，请等待完成或使用不同的 agent_id"

    # 获取父 agent 的工作空间（子 agent 共享）
    parent_workspace = get_workspace() or Workspace("default")
    
    # 检查是否需要创建沙箱
    sandbox_enabled = is_sandbox_enabled(agent_id)
    
    logger.info(f"启动子 agent: {agent_id}, 共享工作空间: {parent_workspace.root}, 沙箱: {sandbox_enabled}")

    def _run():
        original_cwd = os.getcwd()
        sandbox_created = False
        
        try:
            # 创建沙箱（如果启用）
            if sandbox_enabled:
                from pathlib import Path
                try:
                    from qrclaw.sandbox import create_sandbox
                    create_sandbox(
                        agent_id=agent_id,
                        workspace=Path(parent_workspace.root),
                    )
                    sandbox_created = True
                    logger.info(f"已为子 agent {agent_id} 创建沙箱")
                except Exception as e:
                    logger.warning(f"为子 agent {agent_id} 创建沙箱失败: {e}")
            
            # 执行子 agent（共享父 agent 的工作空间）
            result = run_sub_agent(task, parent_workspace, agent_id)
            
            with _task_pool_lock:
                _task_pool[agent_id]["status"] = "done"
                _task_pool[agent_id]["result"] = result
            
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
                
        except Exception as e:
            logger.error(f"子 agent {agent_id} 出错: {e}", exc_info=True)
            with _task_pool_lock:
                _task_pool[agent_id]["status"] = "error"
                _task_pool[agent_id]["result"] = f"执行出错: {e}"
            if _console:
                _console.print(f"\n[bold red]子 agent '{agent_id}' 执行出错: {e}[/bold red]\n")
        finally:
            # 销毁沙箱
            if sandbox_created:
                try:
                    from qrclaw.sandbox import destroy_sandbox, sandbox_manager
                    if sandbox_manager.has_sandbox(agent_id):
                        destroy_sandbox(agent_id)
                        logger.info(f"已销毁子 agent {agent_id} 的沙箱")
                except Exception as e:
                    logger.warning(f"销毁子 agent {agent_id} 的沙箱失败: {e}")
            
            # 恢复 cwd
            try:
                os.chdir(original_cwd)
            except Exception:
                pass

    thread = threading.Thread(target=_run, name=f"sub-agent-{agent_id}", daemon=True)

    with _task_pool_lock:
        _task_pool[agent_id] = {
            "status": "running",
            "result": None,
            "thread": thread,
            "sandbox_enabled": sandbox_enabled,
        }

    thread.start()
    
    sandbox_hint = " (沙箱已启用)" if sandbox_enabled else ""
    return f"子 agent '{agent_id}' 已在后台启动{sandbox_hint}，任务：{task[:50]}{'...' if len(task) > 50 else ''}"