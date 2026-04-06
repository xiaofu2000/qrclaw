"""
Skills 注册表

管理所有可用的技能，支持：
- 自动发现和加载
- 轻量级描述注入（System Prompt）
- 完整信息按需加载
- 完全兼容 OpenClaw 的 SKILL.md 格式
- 单例模式：load_from_dir() 只执行一次，后续直接返回缓存
"""

import yaml
from pathlib import Path
from typing import Dict, List, Optional
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.skills.registry")


class Skill:
    """技能定义（OpenClaw SKILL.md 格式）"""

    def __init__(self, name: str, description: str, version: str = "1.0.0",
                 metadata: Dict = None, content: str = "", path: Path = None):
        self.name = name
        self.description = description
        self.version = version
        self.metadata = metadata or {}
        self.content = content
        self.path = path

    @classmethod
    def from_skill_md(cls, skill_md_path: Path) -> "Skill":
        """从 SKILL.md 文件加载技能"""
        try:
            content = skill_md_path.read_text(encoding="utf-8")

            # 解析 YAML frontmatter
            metadata = {}
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    metadata = yaml.safe_load(parts[1]) or {}
                    markdown_content = parts[2].strip()
                else:
                    markdown_content = content
            else:
                markdown_content = content

            skill = cls(
                name=metadata.get("name", skill_md_path.parent.name),
                description=metadata.get("description", ""),
                version=metadata.get("version", "1.0.0"),
                metadata=metadata,
                content=markdown_content,
                path=skill_md_path.parent
            )

            logger.info(f"加载技能: {skill.name} - {skill.description}")
            return skill

        except Exception as e:
            logger.error(f"加载技能失败: {skill_md_path}, 错误: {e}", exc_info=True)
            raise

    def get_lightweight_info(self) -> str:
        """获取轻量级信息（用于 System Prompt）"""
        return f"{self.name} - {self.description}"

    def get_full_info(self) -> str:
        """获取完整信息（按需加载）"""
        lines = [
            f"## 技能：{self.name}",
            f"**描述**：{self.description}",
            f"**版本**：{self.version}",
            "",
            "---",
            "",
            self.content
        ]
        return "\n".join(lines)


class SkillRegistry:
    """
    技能注册表（单例模式）
    
    使用单例模式确保 skills 目录只扫描一次，
    后续调用直接返回缓存的技能列表，大幅提升启动性能。
    
    使用方式：
        registry = SkillRegistry.get_instance()
        registry.load_from_dir(skills_dir)  # 只在第一次执行
    """
    
    _instance: Optional["SkillRegistry"] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        """单例模式：确保只创建一个实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # 确保初始化只执行一次（即使通过 __new__ 创建多个实例）
        if not SkillRegistry._initialized:
            self.skills: Dict[str, Skill] = {}
            self._loaded: bool = False
            self._loaded_dir: Optional[Path] = None
            SkillRegistry._initialized = True
            logger.debug("SkillRegistry 单例初始化")

    @classmethod
    def get_instance(cls) -> "SkillRegistry":
        """获取单例实例的推荐方式"""
        return cls()

    @classmethod
    def reset(cls):
        """重置单例（主要用于测试）"""
        cls._instance = None
        cls._initialized = False

    def load_from_dir(self, skills_dir: Path):
        """
        从目录加载所有技能（skills_dir 由 Workspace 提供）
        
        首次调用执行实际加载，后续调用直接返回缓存，
        除非传入不同的 skills_dir（会重新加载）。
        """
        if not skills_dir:
            return

        # 如果已经加载过，且目录未变化，直接返回缓存
        if self._loaded and self._loaded_dir == skills_dir:
            logger.debug(f"使用缓存的技能列表（已加载 {len(self.skills)} 个技能）")
            return

        # 目录变化了，需要重新加载
        if self._loaded and self._loaded_dir != skills_dir:
            logger.info(f"Skills 目录变更，重新加载: {self._loaded_dir} -> {skills_dir}")
            self.skills.clear()
            self._loaded = False

        if not skills_dir.exists():
            logger.info(f"Skills 目录不存在，创建：{skills_dir}")
            skills_dir.mkdir(parents=True, exist_ok=True)
            self._loaded = True
            self._loaded_dir = skills_dir
            return

        # 扫描所有技能目录
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            # 查找 SKILL.md 或 skill.md
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                skill_md = skill_dir / "skill.md"

            if not skill_md.exists():
                logger.debug(f"跳过目录（缺少 SKILL.md）：{skill_dir.name}")
                continue

            try:
                skill = Skill.from_skill_md(skill_md)
                self.skills[skill.name] = skill
                logger.debug(f"注册技能：{skill.name}")
            except Exception as e:
                logger.error(f"加载技能失败：{skill_dir}, 错误：{e}", exc_info=True)

        self._loaded = True
        self._loaded_dir = skills_dir
        logger.info(f"加载了 {len(self.skills)} 个技能")

    def get_skills_list(self) -> List[str]:
        """获取技能列表（轻量级描述）"""
        return [skill.get_lightweight_info() for skill in self.skills.values()]

    def get_skill(self, name: str) -> Optional[Skill]:
        """获取技能完整信息"""
        return self.skills.get(name)

    def has_skill(self, name: str) -> bool:
        """检查技能是否存在"""
        return name in self.skills

    def get_all_skills(self) -> Dict[str, Skill]:
        """获取所有技能"""
        return self.skills

    def is_loaded(self) -> bool:
        """检查是否已加载"""
        return self._loaded
