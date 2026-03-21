"""
Skill 安装工具（CLI 层）

提供 /skill install 命令的实现，实际安装逻辑由 install-skill skill 定义。
这里只做参数解析、URL 转换和下载执行。
"""

import subprocess
from pathlib import Path
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.skills.installer")

# Skills 目录
SKILLS_DIR = Path.home() / ".qrclaw" / "skills"


def install_from_clawhub(skill_name: str, skills_dir: Path = None) -> bool:
    """
    从 ClawHub 安装 skill

    ClawHub 是 OpenClaw 的官方 skill 仓库：
    https://github.com/openclaw/skills

    Args:
        skill_name: skill 名称或 slug，例如：
            - "agent-self-reflection"
            - "brennerspear/agent-self-reflection"
        skills_dir: 安装目录，默认为 ~/.qrclaw/skills/

    Returns:
        bool: 是否成功
    """
    if skills_dir is None:
        skills_dir = SKILLS_DIR

    skills_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 转换为 GitHub URL
        if "/" not in skill_name:
            # 格式: agent-self-reflection
            github_url = f"https://github.com/openclaw/skills/tree/main/skills/{skill_name}"
            skill_name = skill_name.split("/")[-1]
        else:
            # 格式: brennerspear/agent-self-reflection
            github_url = f"https://github.com/openclaw/skills/tree/main/skills/{skill_name}"
            skill_name = skill_name.split("/")[-1]

        return install_from_github(github_url, skills_dir)

    except Exception as e:
        logger.error(f"从 ClawHub 安装失败: {skill_name}, 错误: {e}", exc_info=True)
        return False


def install_from_github(github_url: str, skills_dir: Path = None) -> bool:
    """
    从 GitHub URL 安装 skill

    Args:
        github_url: GitHub URL，例如：
            - "https://github.com/openclaw/skills/tree/main/skills/agent-self-reflection"
        skills_dir: 安装目录，默认为 ~/.qrclaw/skills/

    Returns:
        bool: 是否成功
    """
    if skills_dir is None:
        skills_dir = SKILLS_DIR

    skills_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 提取 skill 名称
        skill_name = github_url.rstrip("/").split("/")[-1]
        skill_path = skills_dir / skill_name

        # 检查是否已存在
        if skill_path.exists():
            logger.warning(f"Skill 已存在: {skill_name}，跳过安装")
            return True

        # 转换为 raw URL
        # https://github.com/user/repo/tree/main/path/to/skill
        # → https://raw.githubusercontent.com/user/repo/main/path/to/skill/SKILL.md
        raw_url = github_url.replace("github.com", "raw.githubusercontent.com")
        raw_url = raw_url.replace("/tree/", "/")
        skill_md_url = f"{raw_url}/SKILL.md"

        logger.info(f"下载 skill: {skill_name}")
        logger.debug(f"URL: {skill_md_url}")

        # 创建 skill 目录
        skill_path.mkdir(parents=True, exist_ok=True)

        # 使用 curl 下载（通过 subprocess）
        result = subprocess.run(
            ["curl", "-sL", skill_md_url, "-o", str(skill_path / "SKILL.md")],
            capture_output=True,
            timeout=30
        )

        if result.returncode == 0:
            # 验证文件是否下载成功
            skill_md = skill_path / "SKILL.md"
            if skill_md.exists() and skill_md.stat().st_size > 0:
                logger.info(f"✅ 安装成功: {skill_name}")
                return True
            else:
                logger.error("下载的文件为空或不存在")
                # 清理空目录
                if skill_path.exists() and not any(skill_path.iterdir()):
                    skill_path.rmdir()
                return False
        else:
            error_msg = result.stderr.decode() if result.stderr else "未知错误"
            logger.error(f"下载失败: {error_msg}")
            # 清理空目录
            if skill_path.exists() and not any(skill_path.iterdir()):
                skill_path.rmdir()
            return False

    except subprocess.TimeoutExpired:
        logger.error("下载超时")
        return False
    except Exception as e:
        logger.error(f"从 GitHub 安装失败: {github_url}, 错误: {e}", exc_info=True)
        return False


def install_from_local(local_path: str, skills_dir: Path = None) -> bool:
    """
    从本地路径安装 skill

    Args:
        local_path: 本地路径，例如："/tmp/my-skill"
        skills_dir: 安装目录，默认为 ~/.qrclaw/skills/

    Returns:
        bool: 是否成功
    """
    if skills_dir is None:
        skills_dir = SKILLS_DIR

    skills_dir.mkdir(parents=True, exist_ok=True)

    try:
        source_path = Path(local_path).expanduser().resolve()

        # 验证源路径
        if not source_path.exists():
            logger.error(f"源路径不存在: {source_path}")
            return False

        skill_md = source_path / "SKILL.md"
        if not skill_md.exists():
            logger.error(f"找不到 SKILL.md: {skill_md}")
            return False

        # 复制到 skills 目录
        skill_name = source_path.name
        target_path = skills_dir / skill_name

        if target_path.exists():
            logger.warning(f"Skill 已存在: {skill_name}，跳过安装")
            return True

        import shutil
        shutil.copytree(source_path, target_path)

        logger.info(f"✅ 安装成功: {skill_name}")
        return True

    except Exception as e:
        logger.error(f"从本地安装失败: {local_path}, 错误: {e}", exc_info=True)
        return False


# 保留旧函数名以兼容
def install_openclaw_skill(github_url: str, skills_dir: Path = None) -> bool:
    """向后兼容：从 GitHub 安装 OpenClaw skill"""
    return install_from_github(github_url, skills_dir)