"""
System Prompt 构建模块。
参考 OpenClaw 的分段设计，每个模块独立，最后组合。
"""
import os
import platform
from javaclaw.config import AGENT_NAME


def _build_tooling_section(tool_names: list[str]) -> str:
    if not tool_names:
        return ""
    lines = ["## Tooling", "你可以使用以下工具："]
    for name in tool_names:
        lines.append(f"- {name}")
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
    ])


def build_system_prompt(tool_names: list[str] | None = None) -> str:
    """构建完整的 system prompt"""
    sections = [
        f"你是 {AGENT_NAME}，一个运行在用户本地的自主 AI Agent。",
        "",
        #_build_tooling_section(tool_names or []),
        "",
        _build_behavior_section(),
        "",
        _build_safety_section(),
        "",
        _build_workspace_section(),
    ]
    return "\n".join(sections)
