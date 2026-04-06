"""
Skills 技能系统

提供可扩展的技能管理，完全兼容 OpenClaw 的 SKILL.md 格式。
采用单例模式，确保 skills 目录只扫描一次，大幅提升启动性能。
"""

from qrclaw.skills.registry import SkillRegistry, Skill

__all__ = ["SkillRegistry", "Skill", "get_skill_registry"]

# 便捷函数：获取单例实例
def get_skill_registry() -> SkillRegistry:
    """获取 SkillRegistry 单例实例（推荐方式）"""
    return SkillRegistry.get_instance()
