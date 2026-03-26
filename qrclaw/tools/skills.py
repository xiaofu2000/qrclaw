"""
内置工具：Skills 相关工具
"""

import json
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.skills.registry import SkillRegistry
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.skills")


# ── 参数模型 ──────────────────────────────────────────────

class UseSkillArgs(BaseModel):
    skill_name: str = Field(description="要使用的技能名称，例如 analyze-project")
    args: dict = Field(default_factory=dict, description="技能参数，根据技能定义传入")


def get_skill_registry() -> SkillRegistry:
    """获取当前 agent 的 Skill Registry（从 agent 的 workspace 加载）"""
    from qrclaw.agent import get_session
    from qrclaw.workspace import Workspace
    session = get_session()
    # 通过 session 路径反推 workspace（sessions_dir 的上两级是 agent root）
    if session is not None:
        agent_root = session._path.parent.parent
        skills_dir = agent_root / "skills"
    else:
        skills_dir = Workspace("default").skills_dir
    registry = SkillRegistry()
    registry.load_from_dir(skills_dir)
    return registry


# ── 工具函数 ──────────────────────────────────────────────

@register(description="使用指定的技能（Skill）来完成复杂任务，技能是预定义的工作流", args_model=UseSkillArgs)
def use_skill(skill_name: str, args: dict = None) -> str:
    """
    使用技能

    Args:
        skill_name: 技能名称
        args: 技能参数

    Returns:
        str: 技能的完整信息和执行指导
    """
    if args is None:
        args = {}

    logger.debug(f"使用技能: {skill_name}, 参数: {args}")

    # 获取技能注册表
    registry = get_skill_registry()

    # 检查技能是否存在
    if not registry.has_skill(skill_name):
        available_skills = ", ".join(registry.get_skills_list())
        error_msg = f"错误：找不到技能 '{skill_name}'\n\n可用技能：\n{available_skills}"
        logger.warning(error_msg)
        return error_msg

    # 获取技能完整信息
    skill = registry.get_skill(skill_name)

    # 构建技能执行指导
    lines = [
        f"## 开始执行技能：{skill.name}",
        "",
        skill.get_full_info(),
        "",
        "## 执行指导",
        "请按照上述步骤，逐步完成这个技能的任务。你可以：",
        "- 使用 read_file、write_file、run_shell 等工具",
        "- 按照步骤顺序执行",
        "- 根据实际情况灵活调整",
        "",
        f"**用户提供的参数**：{json.dumps(args, ensure_ascii=False, indent=2) if args else '无'}",
    ]

    result = "\n".join(lines)
    logger.info(f"注入技能完整信息: {skill_name}")

    return result