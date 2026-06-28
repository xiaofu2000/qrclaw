"""
PromptBuilder —— system prompt 组装器

职责：
- 收拢 system prompt 的分段拼装逻辑
- 统一管理默认行为、工具说明、技能、记忆、环境信息
- 让 ContextManager 只负责缓存和按角色取消息，不再背负拼装细节
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

from qrclaw.config import AGENT_NAME
from qrclaw.logger import get_logger
from qrclaw.memory.wiki import WikiMemory
from qrclaw.skills.registry import SkillRegistry
from qrclaw.tools.registry import get_schemas

logger = get_logger("qrclaw.prompt_builder")

_TOOL_DESCRIPTIONS = {
    "read_file": "读取本地文件内容，需要查看文件时使用",
    "write_file": "写入或创建本地文件，用户要求保存代码/文档时直接使用",
    "list_directory": "列出目录下的文件和子目录，不知道目录结构时先用它探索",
    "web_search": "联网搜索获取最新信息，查找文档、新闻、技术资料时使用",
    "web_fetch": "访问指定网页并提取纯净的 Markdown 正文，适合阅读文章、文档",
    "run_shell": "执行 shell 命令，需要运行程序、安装依赖、操作系统时使用",
    "write_wiki_page": "新建或更新 Wiki 记忆页面，用于记录用户偏好、项目配置、技术决策等需要跨会话复用的知识。正文可用 [[页面名]] 引用其他页面",
    "read_wiki_page": "读取指定 Wiki 页面的完整内容",
    "list_wiki_pages": "列出所有 Wiki 页面，可按标签过滤",
    "search_wiki": "在 Wiki 中搜索关键词，返回匹配的页面",
    "delete_wiki_page": "删除指定的 Wiki 页面",
    "use_skill": "使用指定的技能（Skill）来完成复杂任务，技能是预定义的工作流",
    "spawn_agent": "在后台启动子 agent 并行执行独立子任务，任务可拆分时批量调用，子 agent 完成后结果自动打印",
    "wait_agents": "等待所有后台子 agent 完成，返回各子 agent 的结构化摘要，再根据摘要决定是否用 read_file 查看详情",
}


def _build_tooling_section(tool_names: list[str]) -> str:
    if not tool_names:
        return ""

    dynamic_descriptions = {}
    try:
        for schema in get_schemas():
            name = schema.get("function", {}).get("name")
            desc = schema.get("function", {}).get("description")
            if name and desc:
                dynamic_descriptions[name] = desc
    except Exception as e:
        logger.warning(f"获取工具 schema 失败: {e}")

    lines = ["## 可用工具", "工具名称区分大小写，按名称精确调用："]
    for name in tool_names:
        desc = dynamic_descriptions.get(name) or _TOOL_DESCRIPTIONS.get(name, "")
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(lines)


def _build_identity_section(agent_file: Path | None) -> str:
    if agent_file is None or not agent_file.exists():
        return ""
    try:
        content = agent_file.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        if content.startswith("---"):
            parts = content.split("---", 2)
            content = parts[2].strip() if len(parts) >= 3 else content
        if not content:
            return ""
        logger.info(f"注入 AGENT.md 身份定义: {agent_file}")
        return "\n".join(["## Agent 身份", content])
    except Exception as e:
        logger.warning(f"读取 AGENT.md 失败: {e}")
        return ""


def _build_behavior_section(is_sub_agent: bool = False) -> str:
    lines = [
        "## 行为准则",
        "- 优先用工具完成任务，不要凭空猜测文件内容或命令结果",
        "- 每次只调用一个工具，观察结果后再决定下一步",
        "- 任务完成后，用简洁的语言告诉用户做了什么、结果是什么",
        "- 遇到错误时，说明原因并给出解决建议",
        "- 语言简洁，不废话",
        "- 默认输出纯文本汇报，不要主动生成 Markdown 文档或 .md 文件",
        "- 只有用户明确要求写成 md / markdown / 文档成品时，才使用文档体裁或创建文档文件",
        "",
        "## 工具调用风格",
        "- 常规、低风险的工具调用直接执行，不要先解释再询问用户",
        "- 只在删除等敏感操作、或用户明确要求时，先说明再执行",
        "- 当有专用工具可以完成某个操作时，直接用工具，不要让用户自己去跑命令",
    ]

    if is_sub_agent:
        lines.extend([
            "",
            "## 你是执行具体任务的子 Agent",
            "你的角色是向整个系统交付高信息密度的执行结果，不要写成长文档。",
            "输出应优先包含：状态、关键发现、产出、阻塞点、下一步建议。",
            "如果用户明确要求生成文档，再按文档格式输出；否则默认直接汇报。",
        ])

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


def _build_memory_section(memory_dir: Path | None, wiki_context: str = "") -> str:
    sections = []
    if memory_dir is not None and memory_dir.exists():
        wiki = WikiMemory.for_workspace(memory_dir)
        content = wiki.load_index()
        if content and "暂无页面" not in content:
            logger.info("注入 Wiki 记忆索引到 system prompt")
            sections.extend([
                "## Wiki 记忆",
                "以下是你的 Wiki 知识库索引，记录了跨会话的重要知识。",
                "需要详细内容时，使用 read_wiki_page 读取对应页面。",
                "",
                content,
            ])

    if wiki_context:
        logger.info("注入 Wiki 查询上下文到 system prompt")
        sections.extend([
            "## Wiki 记忆",
            "以下是你的 Wiki 知识库索引，记录了跨会话的重要知识。",
            "需要详细内容时，使用 read_wiki_page 读取对应页面。",
            "",
            wiki_context,
        ])

    return "\n".join(sections) if sections else ""


def _build_heartbeat_section(heartbeat_file: Path | None = None) -> str:
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
    skills_list = skill_registry.get_skills_list()
    if not skills_list:
        return ""

    logger.info(f"注入 {len(skills_list)} 个技能到 system prompt")
    lines = ["## 可用技能", "以下是你可用的技能（Skills），用于完成复杂任务：", ""]
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


def build_system_prompt(
    heartbeat_file: Path | None = None,
    is_sub_agent: bool = False,
    agent_file: Path | None = None,
    skills_dir: Path | None = None,
    memory_dir: Path | None = None,
    wiki_context: str = "",
) -> str:
    tool_names = [s["function"]["name"] for s in get_schemas()]
    skill_registry = SkillRegistry()
    if skills_dir:
        skill_registry.load_from_dir(skills_dir)

    sections = [
        f"你是 {AGENT_NAME}，一个运行在用户本地的自主 AI Agent。",
        "",
        _build_identity_section(agent_file),
        "",
        _build_tooling_section(tool_names),
        "",
        _build_skills_section(skill_registry),
        "",
        _build_behavior_section(is_sub_agent=is_sub_agent),
        "",
        _build_safety_section(),
        "",
        _build_workspace_section(),
        "",
        _build_memory_section(memory_dir, wiki_context),
        "",
        _build_heartbeat_section(heartbeat_file),
    ]
    return "\n".join(sections)
