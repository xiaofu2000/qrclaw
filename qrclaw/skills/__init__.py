"""
Skills 技能系统

提供可扩展的技能管理，完全兼容 OpenClaw 的 SKILL.md 格式。
"""

from qrclaw.skills.registry import SkillRegistry, Skill

__all__ = ["SkillRegistry", "Skill"]