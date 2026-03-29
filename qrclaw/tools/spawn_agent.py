"""
spawn_agent 工具

允许主 agent 并行创建多个子 agent 执行任务。
子 agent 在后台线程运行，立即返回，不阻塞主 agent。

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


# 父子 agent 映射
_parent_agent_map: dict = {}
_parent_agent_map_lock = threading.Lock()


def get_parent_agent_id(sub_agent_id: str) -> str | None:
    """获取子 agent 的父 agent ID"""
    return _parent_agent_map.get(sub_agent_id)


def set_parent_agent(sub_agent_id: str, parent_agent_id: str):
    """设置父子关系"""
    with _parent_agent_map_lock:
        _parent_agent_map[sub_agent_id] = parent_agent_id


def clear_parent_agent(sub_agent_id: str):
    """清除父子关系"""
    with _parent_agent_map_lock:
        _parent_agent_map.pop(sub_agent_id, None)


def _create_sub_agent_sandbox(agent_id: str, workspace) -> bool:
    """为子 agent 创建沙箱"""
    try:
        from qrclaw.sandbox import create_sandbox
        from pathlib import Path
        
        create_sandbox(
            agent_id=agent_id,
            workspace=Path(workspace.root),
        )
        
        logger.info(f"已为子 agent {agent_id} 创建沙箱")
        return True
        
    except Exception as e:
        logger.warning(f"为子 agent {agent_id} 创建沙箱失败: {e}")
        return False


def _destroy_sub_agent_sandbox(agent_id: str):
    """销毁子 agent 的沙箱"""
    try:
        from qrclaw.sandbox import destroy_sandbox, sandbox_manager
        
        if sandbox_manager.has_sandbox(agent_id):
            destroy_sandbox(agent_id)
            logger.info(f"已销毁子 agent {agent_id} 的沙箱")
            
    except Exception as e:
        logger.warning(f"销毁子 agent {agent_id} 的沙箱失败: {e}")


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
    from qrclaw.workspace import Workspace, ensure_workspace_cwd
    from qrclaw.sandbox import is_sandbox_enabled

    # 子 agent 不允许再派生子 agent
    if is_sub_agent():
        return "错误：子 agent 不允许再派生子 agent，这会导致无限嵌套。请直接执行任务，不要使用 spawn_agent。"

    # 检查是否已在运行
    with _task_pool_lock:
        if agent_id in _task_pool and _task_pool[agent_id]["status"] == "running":
            return f"子 agent '{agent_id}' 已在运行中，请等待完成或使用不同的 agent_id"

    # 获取工作空间
    main_workspace = get_workspace() or Workspace("default")
    sub_workspace = main_workspace.sub_agent(agent_id)
    
    # 记录父子关系
    set_parent_agent(agent_id, main_workspace.agent_id)
    
    # 检查是否需要创建沙箱
    sandbox_enabled = is_sandbox_enabled(agent_id)
    
    logger.info(f"启动子 agent: {agent_id}, 工作空间: {sub_workspace.root}, 沙箱: {sandbox_enabled}")

    def _run():
        original_cwd = os.getcwd()
        sandbox_created = False
        
        try:
            cwd_changed = ensure_workspace_cwd(sub_workspace)
            if cwd_changed:
                logger.info(f"子 agent {agent_id} 已切换 cwd 到: {sub_workspace.root}")
            
            # 创建沙箱
            if sandbox_enabled:
                sandbox_created = _create_sub_agent_sandbox(agent_id, sub_workspace)
            
            result = run_sub_agent(task, sub_workspace)
            
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
                _destroy_sub_agent_sandbox(agent_id)
            
            # 清理父子关系
            clear_parent_agent(agent_id)
            
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