from __future__ import annotations

from dataclasses import dataclass, field
from qrclaw.memory.context.plan.step_result import StepResult


@dataclass
class PlanState:
    """当前正在执行的计划状态。"""

    goal: str
    past_steps: list[StepResult] = field(default_factory=list)
    remaining: list = field(default_factory=list)
    project_path: str = ""
    wiki_context: str = ""
