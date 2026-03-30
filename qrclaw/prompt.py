"""
System Prompt 构建模块。
参考 OpenClaw 的分段设计，每个模块独立，最后组合。
"""
import os
import platform
from pathlib import Path
from qrclaw.config import AGENT_NAME
from qrclaw.memory import LongTermMemory
from qrclaw.skills.registry import SkillRegistry
from qrclaw.tools.registry import get_schemas
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.prompt")

_TOOL_DESCRIPTIONS = {
    "read_file":       "读取本地文件内容，需要查看文件时使用",
    "write_file":      "写入或创建本地文件，用户要求保存代码/文档时直接使用",
    "list_directory":  "列出目录下的文件和子目录，不知道目录结构时先用它探索",
    "web_search":      "联网搜索获取最新信息，查找文档、新闻、技术资料时使用",
    "web_fetch":       "访问指定网页并提取纯净的 Markdown 正文，适合阅读文章、文档",
    "run_shell":       "执行 shell 命令，需要运行程序、安装依赖、操作系统时使用",
    "write_memory":    "写入中期记忆，仅用于记录用户偏好、项目配置等需要跨会话复用的信息，任务结果、调研报告等不要写入",
    "read_memory":     "读取中期记忆，查看之前记录的用户偏好或配置信息",
    "review_memory":   "审查中期记忆，识别过时、重复内容，支持分析和清理操作。建议定期调用维护记忆质量",
    "use_skill":       "使用指定的技能（Skill）来完成复杂任务，技能是预定义的工作流",
    "create_plan":     "为复杂任务创建执行计划，拆解步骤时标注依赖关系，无依赖的步骤可用 spawn_agent 并行执行",
    "spawn_agent":     "在后台启动子 agent 并行执行独立子任务，任务可拆分时批量调用，子 agent 完成后结果自动打印",
    "wait_agents":     "等待所有后台子 agent 完成，返回各子 agent 的结构化摘要，再根据摘要决定是否用 read_file 查看详情",
}


def _build_tooling_section(tool_names: list[str]) -> str:
    """
    动态构建工具列表。
    优先从 registry 获取实时的 schema 描述，
    如果 registry 里没有（理论上不应该），再回退到 _TOOL_DESCRIPTIONS。
    """
    if not tool_names:
        return ""

    # 1. 动态获取所有已注册工具的描述
    # 格式: {'read_file': '读取本地文件...', 'web_search': '联网搜索...'}
    dynamic_descriptions = {}
    try:
        schemas = get_schemas()
        for schema in schemas:
            name = schema.get("function", {}).get("name")
            desc = schema.get("function", {}).get("description")
            if name and desc:
                dynamic_descriptions[name] = desc
    except Exception as e:
        logger.warning(f"获取工具 schema 失败: {e}")

    lines = ["## 可用工具", "工具名称区分大小写，按名称精确调用："]

    for name in tool_names:
        # 2. 优先用动态描述，没有则用硬编码的备用
        desc = dynamic_descriptions.get(name) or _TOOL_DESCRIPTIONS.get(name, "")

        if desc:
            lines.append(f"- {name}: {desc}")
        else:
            # 如果真的都没有，只显示名字
            lines.append(f"- {name}")

    return "\n".join(lines)


def _build_identity_section(agent_file: Path) -> str:
    """
    从 AGENT.md 加载 agent 身份定义。
    支持可选的 YAML frontmatter，正文直接注入 system prompt。
    文件不存在时返回空字符串，不影响默认行为。
    """
    if agent_file is None or not agent_file.exists():
        return ""
    try:
        content = agent_file.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        # 去掉 YAML frontmatter，只取 markdown 正文
        if content.startswith("---"):
            parts = content.split("---", 2)
            content = parts[2].strip() if len(parts) >= 3 else content
        if not content:
            return ""
        logger.info(f"注入 AGENT.md 身份定义: {agent_file}")
        return "\n".join([
            "## Agent 身份",
            content,
        ])
    except Exception as e:
        logger.warning(f"读取 AGENT.md 失败: {e}")
        return ""


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


def _build_behavior_section(is_sub_agent: bool = False) -> str:
    lines = [
        "## 行为准则",
        "- 优先用工具完成任务，不要凭空猜测文件内容或命令结果",
        "- 每次只调用一个工具，观察结果后再决定下一步",
        "- 任务完成后，用简洁的语言告诉用户做了什么、结果是什么",
        "- 遇到错误时，说明原因并给出解决建议",
        "- 语言简洁，不废话",
        "",
        "## 工具调用风格",
        "- 常规、低风险的工具调用直接执行，不要先解释再询问用户",
        "- 只在以下情况才先说明：删除等敏感操作、用户明确要求时",
        "- 当有专用工具可以完成某个操作时，直接用工具，不要让用户自己去跑命令",
    ]

    if is_sub_agent:
        # 子 agent 专用：汇报压缩规则
        lines.extend([
            "",
            "## ⚠️ 你是子 Agent",
            "你由主 Agent 派生，完成任务后必须向主 Agent 汇报。",
            "",
            "**汇报规则（极重要）：**",
            "1. 只返回结构化摘要，禁止输出完整执行过程",
            "2. 摘要格式：",
            "```",
            "## 任务结果",
            "- 状态：成功/失败/部分完成",
            "- 完成内容：[一段话概括做了什么]",
            "- 关键产出：[文件路径/关键数据，如有]",
            "- 遇到问题：[如有，简述]",
            "```",
            "3. 禁止包含：详细日志、代码片段、长篇解释",
            "4. 控制在 500 字以内",
        ])
    else:
        # 主 agent 专用：create_plan 适用于当前步骤内的轻量分步规划
        lines.extend([
            "",
            "## 何时使用 create_plan",
            "",
            "当前步骤内部需要明确分步执行时，使用 create_plan 做轻量规划：",
            "1. 当前任务需要 3 个及以上有序子步骤",
            "2. 任务需要探索未知环境后才能决定后续步骤",
            "3. 任务失败需要回滚或重试",
            "",
            "注意：是否并行、是否派发子 agent 由外层图结构自动决定，无需在此处手动 spawn_agent。",
            "",
            "**示例**：当前步骤是「分析项目结构」，可先 create_plan 拆解：",
            "  Step 1: list_directory 获取目录树",
            "  Step 2: 读取核心配置文件",
            "  Step 3: 汇总分析结果",
        ])

    return "\n".join(lines)


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


def _build_heartbeat_section(heartbeat_file: Path = None) -> str:
    """构建心跳任务部分"""
    if heartbeat_file is None or not heartbeat_file.exists():
        return ""

    try:
        content = heartbeat_file.read_text(encoding="utf-8")
        if not content.strip():
            return ""

        logger.info("注入心跳任务到 system prompt")
        return "\n".join([
            "## 心跳任务",
            "以下是定期执行的维护任务，请在每次心跳触发时执行：",
            "",
            content,
        ])
    except Exception as e:
        logger.warning(f"读取心跳任务文件失败: {e}")
        return ""


def _build_skills_section(skill_registry: SkillRegistry) -> str:
    """构建技能部分（轻量级描述）"""
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
        "执行规则：",
        "1. 按步骤顺序执行，有依赖的步骤等前置步骤完成后再执行",
        "2. 每完成一步调用 complete_step 标记完成后再继续下一步",
        "3. 所有步骤完成后给出整体总结",
    ])
    return "\n".join(lines)


def build_system_prompt(
    tool_names: list[str] | None = None,
    memory: LongTermMemory = None,
    skill_registry: SkillRegistry | None = None,
    active_plan: dict | None = None,
    heartbeat_file: Path | None = None,
    is_sub_agent: bool = False,
    agent_file: Path | None = None,
) -> str:
    """构建完整的 system prompt

    Args:
        tool_names: 可用工具列表
        memory: 长期记忆实例
        skill_registry: 技能注册表
        active_plan: 当前执行计划
        heartbeat_file: 心跳任务文件路径
        is_sub_agent: 是否是子 agent（影响行为准则）
    """
    sections = [
        f"你是 {AGENT_NAME}，一个运行在用户本地的自主 AI Agent。",
        "",
        _build_identity_section(agent_file),
        "",
        _build_tooling_section(tool_names or []),
        "",
        _build_skills_section(skill_registry or SkillRegistry()),
        "",
        _build_behavior_section(is_sub_agent=is_sub_agent),
        "",
        _build_safety_section(),
        "",
        _build_workspace_section(),
        "",
        _build_memory_section(memory),
        "",
        _build_heartbeat_section(heartbeat_file),
        "",
        _build_plan_section(active_plan),
    ]
    return "\n".join(sections)