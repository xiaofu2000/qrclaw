"""
OpenClaw Skill 安装工具

从 GitHub 安装 OpenClaw 格式的 skill。
"""

import subprocess
from pathlib import Path
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.skills.installer")

# Skills 目录
SKILLS_DIR = Path.home() / ".qrclaw" / "skills"


def install_openclaw_skill(github_url: str, skills_dir: Path = None) -> bool:
    """
    从 GitHub 安装 OpenClaw skill
    
    Args:
        github_url: GitHub URL，例如：
            - "https://github.com/openclaw/skills/tree/main/skills/brennerspear/agent-self-reflection"
            - "openclaw/skills/brennerspear/agent-self-reflection"
        skills_dir: 安装目录，默认为 ~/.qrclaw/skills/
    
    Returns:
        bool: 是否成功
    """
    if skills_dir is None:
        skills_dir = SKILLS_DIR
    
    skills_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # 标准化 URL
        if not github_url.startswith("http"):
            github_url = f"https://github.com/{github_url}"
        
        # 从 URL 提取 skill 名称
        parts = github_url.rstrip("/").split("/")
        
        # 处理不同格式的 URL
        if "tree" in parts:
            # https://github.com/openclaw/skills/tree/main/skills/brennerspear/agent-self-reflection
            skill_name = parts[-1]
        else:
            # https://github.com/openclaw/skills/skills/brennerspear/agent-self-reflection
            skill_name = parts[-1]
        
        skill_path = skills_dir / skill_name
        
        if skill_path.exists():
            logger.warning(f"Skill 已存在: {skill_name}")
            return True
        
        # 转换为 raw URL
        raw_url = github_url.replace("github.com", "raw.githubusercontent.com")
        raw_url = raw_url.replace("/tree/", "/")
        
        # 下载 SKILL.md
        skill_md_url = f"{raw_url}/SKILL.md"
        
        logger.info(f"下载 OpenClaw skill: {skill_name}")
        logger.debug(f"URL: {skill_md_url}")
        
        # 创建 skill 目录
        skill_path.mkdir(parents=True, exist_ok=True)
        
        # 下载 SKILL.md
        result = subprocess.run(
            ["curl", "-sL", skill_md_url, "-o", str(skill_path / "SKILL.md")],
            capture_output=True,
            timeout=30
        )
        
        if result.returncode == 0:
            logger.info(f"安装成功: {skill_name}")
            return True
        else:
            logger.error(f"下载失败: {result.stderr.decode()}")
            # 清理空目录
            if skill_path.exists() and not any(skill_path.iterdir()):
                skill_path.rmdir()
            return False
    
    except subprocess.TimeoutExpired:
        logger.error("下载超时")
        return False
    except Exception as e:
        logger.error(f"安装 OpenClaw skill 失败: {e}", exc_info=True)
        return False


def install_from_clawhub(skill_name: str, skills_dir: Path = None) -> bool:
    """
    从 ClawHub 安装 skill（简化版，直接从 GitHub 下载）
    
    Args:
        skill_name: skill 名称或 slug，例如：
            - "agent-self-reflection"
            - "brennerspear/agent-self-reflection"
        skills_dir: 安装目录，默认为 ~/.qrclaw/skills/
    
    Returns:
        bool: 是否成功
    """
    # 如果不包含 /，假设是 openclaw/skills 仓库中的 skill
    if "/" not in skill_name:
        github_url = f"https://github.com/openclaw/skills/tree/main/skills/{skill_name}"
    else:
        # 如果包含 /，假设格式是 author/skill-name
        author, name = skill_name.split("/", 1)
        github_url = f"https://github.com/openclaw/skills/tree/main/skills/{author}/{name}"
    
    return install_openclaw_skill(github_url, skills_dir)