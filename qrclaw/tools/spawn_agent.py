"""
spawn_agent 工具

允许主 agent 创建子 agent 来执行特定任务。
子 agent 在独立的工作空间中运行，结果返回给主 agent。
"""
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.spawn_agent")


class SpawnAgentArgs(BaseModel):
    agent_id: str = Field(description="子 agent 的 ID，用于标识和隔离工作空间，例如 'coder'、'reviewer'")
    task: str = Field(description="交给子 agent 的任务描述，要清晰具体")


@register(
    description="创建子 agent 执行特定任务，子 agent 有独立的工作空间，完成后返回结果",
    args_model=SpawnAgentArgs,
)
def spawn_agent(agent_id: str, task: str) -> str:
    """
    创建并运行子 agent。

    Args:
        agent_id: 子 agent ID（嵌套在当前 agent 工作空间下）
        task: 子 agent 要执行的任务

    Returns:
        str: 子 agent 的执行结果
    """
    logger.info(f"主 agent 请求创建子 agent: {agent_id}")

    # 通过当前 session 反推主 agent 的 workspace
    from qrclaw.agent import get_session, run_sub_agent
    from qrclaw.workspace import Workspace

    session = get_session()
    if session is not None:
        # session 文件路径：<agent_root>/sessions/<id>.json
        # 向上两级拿到 agent_root
        agent_root = session._path.parent.parent
        main_workspace = Workspace(agent_id="current", _root=agent_root)
    else:
        main_workspace = Workspace("default")

    # 子 agent 工作空间嵌套在主 agent 下
    sub_workspace = main_workspace.sub_agent(agent_id)

    logger.info(f"子 agent 工作空间: {sub_workspace.root}")

    try:
        result = run_sub_agent(task, sub_workspace)
        return f"[子 agent '{agent_id}' 执行结果]\n\n{result}"
    except Exception as e:
        logger.error(f"子 agent {agent_id} 执行失败: {e}", exc_info=True)
        return f"子 agent '{agent_id}' 执行失败: {e}"
