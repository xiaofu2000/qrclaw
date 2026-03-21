"""
Skills 注册表

管理所有可用的技能，支持：
- 自动发现和加载
- 轻量级描述注入（System Prompt）
- 完整信息按需加载
"""

import yaml
from pathlib import Path
from typing import Dict, List, Optional
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.skills.registry")

# Skills 目录
SKILLS_DIR = Path.home() / ".qrclaw" / "skills"


class Skill:
    """技能定义"""
    
    def __init__(self, name: str, description: str, version: str = "1.0.0", 
                 author: str = "", inputs: Dict = None, steps: List = None):
        self.name = name
        self.description = description
        self.version = version
        self.author = author
        self.inputs = inputs or {}
        self.steps = steps or []
        self.path: Optional[Path] = None
    
    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "Skill":
        """从 YAML 文件加载技能"""
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        skill = cls(
            name=data.get("name", yaml_path.parent.name),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", ""),
            inputs=data.get("inputs", {}),
            steps=data.get("steps", [])
        )
        skill.path = yaml_path.parent
        
        logger.info(f"加载技能: {skill.name} - {skill.description}")
        return skill
    
    def get_lightweight_info(self) -> str:
        """获取轻量级信息（用于 System Prompt）"""
        return f"{self.name} - {self.description}"
    
    def get_full_info(self) -> str:
        """获取完整信息（按需加载）"""
        lines = [
            f"## 技能：{self.name}",
            f"**描述**：{self.description}",
            f"**版本**：{self.version}",
        ]
        
        if self.author:
            lines.append(f"**作者**：{self.author}")
        
        if self.inputs:
            lines.append("\n**输入参数**：")
            for param, info in self.inputs.items():
                required = "必需" if info.get("required", False) else "可选"
                default = f"（默认：{info.get('default')}）" if "default" in info else ""
                lines.append(f"- {param} ({info.get('type', 'string')}, {required}){default}：{info.get('description', '')}")
        
        if self.steps:
            lines.append("\n**执行步骤**：")
            for i, step in enumerate(self.steps, 1):
                if "tool" in step:
                    lines.append(f"{i}. 调用工具：{step['tool']}")
                elif "prompt" in step:
                    lines.append(f"{i}. 执行推理：{step['prompt'][:50]}...")
        
        return "\n".join(lines)


class SkillRegistry:
    """技能注册表"""
    
    def __init__(self):
        self.skills: Dict[str, Skill] = {}
    
    def load_from_dir(self, skills_dir: Path = None):
        """从目录加载所有技能"""
        if skills_dir is None:
            skills_dir = SKILLS_DIR
        
        if not skills_dir.exists():
            logger.info(f"Skills 目录不存在，创建：{skills_dir}")
            skills_dir.mkdir(parents=True, exist_ok=True)
            return
        
        # 扫描所有技能目录
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            
            yaml_path = skill_dir / "skill.yaml"
            if not yaml_path.exists():
                logger.warning(f"技能目录缺少 skill.yaml：{skill_dir}")
                continue
            
            try:
                skill = Skill.from_yaml(yaml_path)
                self.skills[skill.name] = skill
                logger.debug(f"注册技能：{skill.name}")
            except Exception as e:
                logger.error(f"加载技能失败：{skill_dir}, 错误：{e}", exc_info=True)
        
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