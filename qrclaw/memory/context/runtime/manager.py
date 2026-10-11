from __future__ import annotations

import threading

from qrclaw.config import COMPRESS_THRESHOLD
from qrclaw.logger import get_logger
from qrclaw.memory.compression.compressor import summarize
from qrclaw.memory.context.prompt.builder import build_react_messages, build_router_messages
from qrclaw.memory.context.plan.controller import PlanController
from qrclaw.memory.context.session.store import SessionStore
from qrclaw.memory.context.session.session import Session
from qrclaw.memory.context.prompt.cache import SystemPromptCache
from qrclaw.prompt_builder import build_system_prompt
from qrclaw.workspace import Workspace

logger = get_logger("qrclaw.memory.context_manager")

_thread_local = threading.local()


def init_context_manager(
    session: Session,
    workspace: Workspace,
    is_sub_agent: bool = False,
) -> "ContextManager":
    ctx = ContextManager(session, workspace, is_sub_agent)
    _thread_local.ctx = ctx
    return ctx


def get_context_manager() -> "ContextManager":
    ctx = getattr(_thread_local, "ctx", None)
    if ctx is None:
        raise RuntimeError("当前线程未初始化 ContextManager，请先调用 init_context_manager()")
    return ctx


class ContextManager:
    """轻门面：只负责串联 session / plan / prompt cache。"""

    def __init__(self, session: Session, workspace: Workspace, is_sub_agent: bool = False):
        self.sessions = SessionStore(session)
        self.workspace = workspace
        self.is_sub_agent = is_sub_agent
        self._lock = threading.Lock()
        self._plan = PlanController()
        self._prompt_cache = SystemPromptCache()

    def add(self, message: dict):
        self.sessions.add(message)

    def set_plan(self, goal: str, steps: list, project_path: str = ""):
        with self._lock:
            self._plan.set_plan(goal, steps, project_path)

    def set_wiki_context(self, wiki_context: str) -> None:
        with self._lock:
            if self._plan.state is None:
                return
            self._plan.set_wiki_context(wiki_context)
        if wiki_context:
            self.invalidate_cache()

    def add_step_result(self, result) -> None:
        with self._lock:
            self._plan.add_step_result(result)

    def update_remaining(self, steps: list):
        with self._lock:
            self._plan.update_remaining(steps)

    def clear_plan(self):
        with self._lock:
            self._plan.clear()
        self.invalidate_cache()

    @property
    def plan_state(self):
        return self._plan.state

    def invalidate_cache(self) -> None:
        self._prompt_cache.invalidate()

    def _get_system_prompt(self) -> str:
        cached = self._prompt_cache.get()
        if cached is not None:
            return cached

        wiki_context = ""
        with self._lock:
            if self._plan.state is not None:
                wiki_context = self._plan.state.wiki_context

        prompt = build_system_prompt(
            heartbeat_file=self.workspace.heartbeat_file,
            is_sub_agent=self.is_sub_agent,
            agent_file=self.workspace.agent_file,
            skills_dir=self.workspace.skills_dir,
            memory_dir=self.workspace.memory_dir,
            wiki_context=wiki_context,
        )
        self._prompt_cache.set(prompt)
        return prompt

    def build_messages(self, role: str, **kwargs) -> list[dict]:
        if role == "react":
            return build_react_messages(self._get_system_prompt(), self.sessions.messages)
        if role == "router":
            return build_router_messages(self.sessions.messages, kwargs["route_instruction"])
        if role == "replanner":
            return self._plan.build_replanner_messages(**kwargs)
        raise ValueError(f"未知 role: {role}")

    def compress_if_needed(self):
        from qrclaw.memory.token_utils import count_text_tokens

        system_prompt_tokens = count_text_tokens(self._get_system_prompt())
        total_tokens = self.sessions.prompt_tokens + system_prompt_tokens
        if total_tokens > COMPRESS_THRESHOLD:
            summarize(self.sessions.session)
            self.invalidate_cache()
