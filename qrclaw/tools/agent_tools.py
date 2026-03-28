"""
Agent 管理工具
"""
from pathlib import Path
import shutil
import yaml
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.workspace import AGENTS_ROOT
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.agent_tools")

# 权限配置文件路径
PERMISSIONS_FILE = Path.home() / ".qrclaw" / "permissions.yaml"


class CreateAgentArgs(BaseModel):
    name: str = Field(description="Agent 名称，只能包含字母、数字、中划线和下划线")


class DeleteAgentArgs(BaseModel):
    name: str = Field(description="要删除的 Agent 名称")


def _is_valid_name(name: str) -> bool:
    """检查 agent 名称是否合法"""
    import re
    return bool(re.match(r'^[a-zA-Z0-9_-]+$', name))


def _remove_agent_permissions(name: str):
    """从 permissions.yaml 中移除 agent 的权限配置"""
    if not PERMISSIONS_FILE.exists():
        return
    
    try:
        with open(PERMISSIONS_FILE, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        
        if "agents" not in config or name not in config["agents"]:
            return
        
        # 移除该 agent 的配置
        del config["agents"][name]
        
        with open(PERMISSIONS_FILE, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        
        logger.info(f"已从 permissions.yaml 中移除 agent '{name}' 的权限配置")
    
    except Exception as e:
        logger.warning(f"清理权限配置失败: {e}")


@register(description="创建一个新的 Agent，会自动创建工作目录结构（sessions/logs/skills）和初始化文件", args_model=CreateAgentArgs)
def create_agent(name: str) -> str:
    """
    创建一个新的 Agent 工作空间。

    目录结构：
    ~/.qrclaw/agents/<name>/
    ├── sessions/    # 会话历史
    ├── logs/        # 日志文件
    ├── skills/      # 技能脚本
    └── MEMORY.md    # 中期记忆
    
    注意：新创建的 Agent 默认为 scoped 权限（只能访问自己的工作目录）。
    如需访问外部目录，需手动编辑 ~/.qrclaw/permissions.yaml 配置白名单。
    """
    logger.info(f"创建 Agent: {name}")

    # 验证名称
    if not _is_valid_name(name):
        error_msg = f"错误：Agent 名称不合法，只能包含字母、数字、中划线和下划线: {name}"
        logger.warning(error_msg)
        return error_msg

    # 检查是否已存在
    agent_dir = AGENTS_ROOT / name
    if agent_dir.exists():
        error_msg = f"错误：Agent '{name}' 已存在: {agent_dir}"
        logger.warning(error_msg)
        return error_msg

    try:
        # 创建目录结构
        (agent_dir / "sessions").mkdir(parents=True, exist_ok=True)
        (agent_dir / "logs").mkdir(parents=True, exist_ok=True)
        (agent_dir / "skills").mkdir(parents=True, exist_ok=True)

        # 创建 MEMORY.md
        memory_file = agent_dir / "MEMORY.md"
        memory_file.write_text(f"# {name} 中期记忆\n\n", encoding="utf-8")

        logger.info(f"Agent '{name}' 创建成功: {agent_dir}")
        return f"✅ Agent '{name}' 创建成功\n\n工作目录: {agent_dir}\n\n目录结构:\n- sessions/  (会话历史)\n- logs/      (日志文件)\n- skills/    (技能脚本)\n- MEMORY.md  (中期记忆)\n\n默认权限: scoped (只能访问自己的工作目录)\n如需访问外部目录，请在 ~/.qrclaw/permissions.yaml 中配置白名单。"

    except Exception as e:
        error_msg = f"错误：创建 Agent 失败: {e}"
        logger.error(f"创建 Agent 失败: {name}, 错误: {e}", exc_info=True)
        return error_msg


@register(description="删除一个 Agent 及其工作目录，同时清理权限配置，此操作不可恢复", args_model=DeleteAgentArgs, confirm=True)
def delete_agent(name: str) -> str:
    """
    删除一个 Agent 及其所有数据。

    警告：此操作会删除会话历史、日志、技能、记忆等所有数据，并清理权限配置，不可恢复！
    """
    logger.info(f"删除 Agent: {name}")

    # 不允许删除 default agent
    if name == "default":
        error_msg = "错误：不能删除 default agent"
        logger.warning(error_msg)
        return error_msg

    # 检查是否存在
    agent_dir = AGENTS_ROOT / name
    if not agent_dir.exists():
        error_msg = f"错误：Agent '{name}' 不存在"
        logger.warning(error_msg)
        return error_msg

    try:
        # 删除整个目录
        shutil.rmtree(agent_dir)
        
        # 清理权限配置
        _remove_agent_permissions(name)
        
        logger.info(f"Agent '{name}' 已删除: {agent_dir}")
        return f"✅ Agent '{name}' 已删除\n\n已移除:\n- 工作目录: {agent_dir}\n- 权限配置: ~/.qrclaw/permissions.yaml\n\n⚠️ 所有数据（会话、日志、技能、记忆）已永久删除。"

    except Exception as e:
        error_msg = f"错误：删除 Agent 失败: {e}"
        logger.error(f"删除 Agent 失败: {name}, 错误: {e}", exc_info=True)
        return error_msg