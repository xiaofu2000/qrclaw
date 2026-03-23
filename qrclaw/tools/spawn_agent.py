"""
spawn_agent 工具

允许主 agent 并行创建多个子 agent 执行任务。
子 agent 在后台线程运行，立即返回，不阻塞主 agent。
通过 wait_agents 工具等待并收集所有结果。
"""
import threading
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.spawn_agent")

# 全局任务池：记录所有子 agent 的状态和结果
# 结构：{ agent_id: { "status": "running"|"done"|"error", "result": str, "thread": Thread } }
_task_pool: dict = {}
_task_pool_lock = threading.Lock()


def get_task_pool() -> dict:
    """获取任务池（供 wait_agents 使用）"""
    return _task_pool


def get_task_pool_lock() -> threading.Lock:
    """获取任务池锁"""
    return _task_pool_lock


class SpawnAgentArgs(BaseModel):
    agent_id: str = Field(description="子 agent 的 ID，例如 'coder'、'reviewer'")
    task: str = Field(description="交给子 agent 的任务描述，要清晰具体")


@register(
    description="在后台启动子 agent 执行任务，立即返回不阻塞，可同时启动多个。用 wait_agents 工具等待结果。",
    args_model=SpawnAgentArgs,
)
def spawn_agent(agent_id: str, task: str) -> str:
    """
    在后台线程启动子 agent，立即返回。

    Args:
        agent_id: 子 agent ID
        task: 子 agent 要执行的任务
    Returns:
        str: 启动确认信息
    """
    from qrclaw.agent import get_workspace, run_sub_agent
    from qrclaw.workspace import Workspace

    # 如果该 agent_id 已在运行，拒绝重复启动
    with _task_pool_lock:
        if agent_id in _task_pool and _task_pool[agent_id]["status"] == "running":
            return f"子 agent '{agent_id}' 已在运行中，请等待完成或使用不同的 agent_id"

    # 直接取当前 workspace，不再靠 session 路径反推
    # 这样无论当前是顶层 agent 还是子 agent，新建的子 agent 都是同级的
    main_workspace = get_workspace() or Workspace("default")
    sub_workspace = main_workspace.sub_agent(agent_id)
    logger.info(f"启动子 agent: {agent_id}, 工作空间: {sub_workspace.root}")

    def _run():
        try:
            result = run_sub_agent(task, sub_workspace)
            with _task_pool_lock:
                _task_pool[agent_id]["status"] = "done"
                _task_pool[agent_id]["result"] = result
            logger.info(f"子 agent {agent_id} 完成")
        except Exception as e:
            logger.error(f"子 agent {agent_id} 出错: {e}", exc_info=True)
            with _task_pool_lock:
                _task_pool[agent_id]["status"] = "error"
                _task_pool[agent_id]["result"] = f"执行出错: {e}"

    thread = threading.Thread(target=_run, name=f"sub-agent-{agent_id}", daemon=True)

    with _task_pool_lock:
        _task_pool[agent_id] = {
            "status": "running",
            "result": None,
            "thread": thread,
        }

    thread.start()
    return f"子 agent '{agent_id}' 已在后台启动，任务：{task[:50]}{'...' if len(task) > 50 else ''}"
