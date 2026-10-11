from __future__ import annotations

from qrclaw.memory.context.plan.state import PlanState
from qrclaw.memory.context.plan.step_result import StepResult
from qrclaw.memory.context.message_builder import build_replanner_messages


class PlanController:
    """只负责 plan 状态，不碰 session / prompt cache。"""

    def __init__(self):
        self._state: PlanState | None = None

    @property
    def state(self) -> PlanState | None:
        return self._state

    def set_plan(self, goal: str, steps: list, project_path: str = "") -> None:
        self._state = PlanState(goal=goal, remaining=list(steps), project_path=project_path)

    def set_wiki_context(self, wiki_context: str) -> None:
        if self._state is None:
            return
        self._state.wiki_context = wiki_context

    def add_step_result(self, result: StepResult) -> None:
        if self._state is None:
            return
        self._state.past_steps.append(result)

    def update_remaining(self, steps: list) -> None:
        if self._state is None:
            return
        self._state.remaining = steps

    def clear(self) -> None:
        self._state = None

    def build_replanner_messages(self, replanner_instruction: str = "") -> list[dict]:
        if self._state is None:
            raise ValueError("plan_state 为空，无法构建 replanner prompt")
        past_text = "\n".join(step.to_context_prompt() for step in self._state.past_steps) or "（无）"
        remaining_text = "\n".join(f"- Step {s.id}: {s.description}" for s in self._state.remaining) or "（无剩余步骤）"
        return build_replanner_messages(
            goal=self._state.goal,
            project_path=self._state.project_path,
            past_steps_text=past_text,
            remaining_steps_text=remaining_text,
            replanner_instruction=replanner_instruction,
        )
