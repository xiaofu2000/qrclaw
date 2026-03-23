"""
System Prompt 构建模块。
参考 OpenClaw 的分段设计，每个模块独立，最后组合。
"""
import os
import platform
from qrclaw.config import AGENT_NAME
from qrclaw.memory import LongTermMemory
from qrclaw.skills.registry import SkillRegistry
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.prompt")

_TOOL_DESCRIPTIONS = {
    "read_file":       "读取本地文件内容，需要查看文件时使用",
    "write_file":      "写入或创建本地文件，用户要求保存代码/文档时直接使用",
    "list_directory":  "列出目录下的文件和子目录，不知道目录结构时先用它探索",
    "web_search":      "联网搜索获取最新信息，查找文档、新闻、技术资料时使用",
    "run_shell":       "执行 shell 命令，需要运行程序、安装依赖、操作系统时使用",
    "write_memory":    "写入中期记忆，仅用于记录用户偏好、项目配置等需要跨会话复用的信息，任务结果、调研报告等不要写入",
    "read_memory":     "读取中期记忆，查看之前记录的用户偏好或配置信息",
    "use_skill":       "使用指定的技能（Skill）来完成复杂任务，技能是预定义的工作流",
    "create_plan":     "为复杂任务创建执行计划，简单任务无需调用，只在多步骤复杂任务时使用",
}


def _build_tooling_section(tool_names: list[str]) -> str:
    if not tool_names:
        return ""
    lines = ["## 可用工具", "工具名称区分大小写，按名称精确调用："]
    for name in tool_names:
        desc = _TOOL_DESCRIPTIONS.get(name, "")
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(lines)


def _build_safety_section() -> str:
    return "\n".join([
        "## 安全边界",
        "- 不执行破坏性命令，例如 rm -rf /、格式化磁盘、删除系统文件",
        "- 不读取敏感文件，例如 ~/.ssh/id_rsa、密钥文件、密码文件",
        "- 执行危险操作前，必须先询问用户确认",
        "- 不尝试绕过任何安全限制",
    ])


def _build_workspace_section() -> str:
    cwd = os.getcwd()
    os_name = platform.system()
    return "\n".join([
        "## 工作环境",
        f"- 操作系统：{os_name}",
        f"- 当前工作目录：{cwd}",
        "- 文件操作默认相对于当前工作目录",
    ])


def _build_behavior_section() -> str:
    return "\n".join([
        "## 行为准则",
        "- 优先用工具完成任务，不要凭空猜测文件内容或命令结果",
        "- 每次只调用一个工具，观察结果后再决定下一步",
        "- 任务完成后，用简洁的语言告诉用户做了什么、结果是什么",
        "- 遇到错误时，说明原因并给出解决建议",
        "- 语言简洁，不废话",
        "",
        "## 工具调用风格",
        "- 常规、低风险的工具调用直接执行，不要先解释再询问用户",
        "- 只在以下情况才先说明：多步骤复杂任务、删除等敏感操作、用户明确要求时",
        "- 当有专用工具可以完成某个操作时，直接用工具，不要让用户自己去跑命令",
    ])


def _build_memory_section(memory: LongTermMemory = None) -> str:
    """构建中期记忆部分"""
    if memory is None:
        memory = LongTermMemory()

    content = memory.load()

    if not content or content.strip() == "# QRClaw 中期记忆":
        # 记忆为空，不注入
        return ""

    logger.info("注入中期记忆到 system prompt")
    return "\n".join([
        "## 中期记忆",
        "以下是你之前记录的重要信息，请在回答时参考：",
        "",
        content,
    ])


def _build_skills_section(skill_registry: SkillRegistry = None) -> str:
    """构建技能部分（轻量级描述）"""
    if skill_registry is None:
        skill_registry = SkillRegistry()
        skill_registry.load_from_dir()

    skills_list = skill_registry.get_skills_list()

    if not skills_list:
        # 没有技能，不注入
        return ""

    logger.info(f"注入 {len(skills_list)} 个技能到 system prompt")

    lines = [
        "## 可用技能",
        "以下是你可用的技能（Skills），用于完成复杂任务：",
        "",
    ]

    for i, skill_info in enumerate(skills_list, 1):
        lines.append(f"{i}. {skill_info}")

    lines.extend([
        "",
        "使用建议：",
        "- 当用户要求完成某个任务时，检查是否有匹配的技能",
        "- 如果有匹配的技能，调用 use_skill 工具来执行",
        "- 例如：use_skill(skill_name='analyze-project', args={'project_path': '.'})",
    ])

    return "\n".join(lines)


def _build_plan_section(active_plan: dict | None) -> str:
    """构建当前执行计划部分"""
    if not active_plan:
        return ""
    lines = [
        "## 当前执行计划",
        f"目标：{active_plan['goal']}",
        "",
        "步骤状态：",
    ]
    for step in active_plan["steps"]:
        status = "✅ 已完成" if step["done"] else "⬜ 待执行"
        lines.append(f"- Step {step['id']}: {step['description']} [{status}]")
    lines.extend([
        "",
        "请按顺序执行待执行的步骤，每完成一步调用 complete_step 标记完成后再继续。",
    ])
    return "\n".join(lines)


def build_system_prompt(
    tool_names: list[str] | None = None,
    memory: LongTermMemory = None,
    skill_registry: SkillRegistry = None,
    active_plan: dict | None = None,
) -> str:
    """构建完整的 system prompt"""
    sections = [
        f"你是 {AGENT_NAME}，一个运行在用户本地的自主 AI Agent。",
        "",
        _build_tooling_section(tool_names or []),
        "",
        _build_skills_section(skill_registry),
        "",
        _build_behavior_section(),
        "",
        _build_safety_section(),
        "",
        _build_workspace_section(),
        "",
        _build_memory_section(memory),
        "",
        _build_plan_section(active_plan),
    ]
    return "\n".join(sections)