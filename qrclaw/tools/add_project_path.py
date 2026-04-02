"""
add_project_path 工具

供主 agent 在对话中遇到新的项目绝对路径时主动注册。
注册后的路径会出现在 system prompt 的工作环境段中，
子 agent 也能通过 ProjectContext 看到。

限制：
- 仅主 agent 可用，子 agent 工具列表中不包含此工具
- additional_paths 最多 3 个，超出时最早的被淘汰（FIFO）
"""
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.add_project_path")


class AddProjectPathArgs(BaseModel):
    path: str = Field(description="要注册的项目绝对路径，例如 /Users/xxx/my-project")


@register(
    description="注册一个新的项目路径到工作环境中。当用户提到新的项目绝对路径时调用此工具，注册后所有工具和子 agent 都能感知到该路径。",
    args_model=AddProjectPathArgs,
)
def add_project_path(path: str) -> str:
    """注册新的项目路径"""
    import os
    from pathlib import Path

    # 校验：必须是绝对路径
    if not os.path.isabs(path):
        return f"错误：{path} 不是绝对路径，请提供绝对路径"

    resolved = str(Path(path).resolve())

    # 校验：路径必须存在
    if not os.path.isdir(resolved):
        return f"错误：目录 {resolved} 不存在"

    from qrclaw.project_context import add_additional_path, get_all_project_paths
    add_additional_path(resolved)

    all_paths = get_all_project_paths()
    paths_display = "\n".join(f"  - {p}" for p in all_paths)
    logger.info(f"注册项目路径: {resolved}")
    return f"已注册项目路径: {resolved}\n当前所有项目路径:\n{paths_display}"
