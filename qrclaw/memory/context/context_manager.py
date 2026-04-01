"""
ContextManager —— 统一上下文管理中心

职责：
- 线程级单例，通过 get_context_manager() 全局获取，无需传参
- 持有主 session、workspace、plan_state
- 为不同角色（react / router / replanner）组装 messages
- 线程安全地管理 plan 执行状态（加锁保护并发写入）
- 压缩判断集中在此，不散落在各节点

用法：
    # 主 agent 初始化时
    init_context_manager(session, workspace, is_sub_agent=False)

    # 任意地方取
    ctx = get_context_manager()
    messages = ctx.build_messages("react")
"""
import threading
from dataclasses import dataclass, field
from qrclaw.memory.context.session import Session, count_tokens
from qrclaw.memory.compression.compressor import summarize
from qrclaw.config import COMPRESS_THRESHOLD
from qrclaw.workspace import Workspace
from qrclaw.prompt import build_system_prompt
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.context_manager")


@dataclass
class PlanState:
    """当前正在执行的计划状态，由 ContextManager 直接持有。"""
    goal: str
    past_steps: list = field(default_factory=list)  # list[StepResult]
    remaining: list = field(default_factory=list)   # list[PlanStep]


# 线程级单例：每个线程（主 agent / 子 agent）持有自己的 ContextManager
_thread_local = threading.local()


def init_context_manager(
    session: Session,
    workspace: Workspace,
    is_sub_agent: bool = False,
) -> "ContextManager":
    """初始化当前线程的 ContextManager，返回实例。"""
    ctx = ContextManager(session, workspace, is_sub_agent)
    _thread_local.ctx = ctx
    logger.debug(f"初始化 ContextManager，is_sub_agent={is_sub_agent}")
    return ctx


def get_context_manager() -> "ContextManager":
    """获取当前线程的 ContextManager，未初始化时抛出异常。"""
    ctx = getattr(_thread_local, "ctx", None)
    if ctx is None:
        raise RuntimeError("当前线程未初始化 ContextManager，请先调用 init_context_manager()")
    return ctx


class ContextManager:

    def __init__(self, session: Session, workspace: Workspace, is_sub_agent: bool = False):
        self.session = session
        self.workspace = workspace
        self.is_sub_agent = is_sub_agent
        self._plan_state: PlanState | None = None
        self._lock = threading.Lock()  # 保护 plan_state 的并发写入

    # ── 消息管理 ──────────────────────────────────────────────────────

    def add(self, message: dict):
        self.session.add(message)

    # ── Plan 状态管理（线程安全） ──────────────────────────────────────

    def set_plan(self, goal: str, steps: list):
        """初始化 plan。"""
        with self._lock:
            self._plan_state = PlanState(goal=goal, remaining=list(steps))
        logger.info(f"plan 初始化：{goal}，共 {len(steps)} 步")

    def add_step_result(self, result) -> None:
        """线程安全地追加一个步骤结果。"""
        with self._lock:
            self._plan_state.past_steps.append(result)
        logger.debug(f"追加步骤结果: step_id={result.step_id}")

    def update_remaining(self, steps: list):
        """Replanner 调用，更新剩余步骤。"""
        with self._lock:
            self._plan_state.remaining = steps

    def clear_plan(self):
        """计划执行完毕，清理 plan_state。"""
        with self._lock:
            self._plan_state = None

    @property
    def plan_state(self) -> PlanState | None:
        return self._plan_state

    # ── 消息组装 ──────────────────────────────────────────────────────

    def build_messages(self, role: str, **kwargs) -> list[dict]:
        """
        为不同角色组装 messages。

        role:
          "react"      → [system] + session.messages
          "router"     → [system] + session.messages + [route_instruction]
          "replanner"  → [user: replanner_prompt]（从 plan_state 自动构建）

        kwargs:
          route_instruction (str): role=router 时必传
        """
        if role == "react":
            return self._build_react_messages()
        elif role == "router":
            return self._build_router_messages(kwargs["route_instruction"])
        elif role == "replanner":
            return self._build_replanner_messages()
        else:
            raise ValueError(f"未知 role: {role}")

    def compress_if_needed(self):
        """检查 token 数，超限则压缩。"""
        messages = [self._make_system_prompt(), *self.session.messages]
        if count_tokens(messages) > COMPRESS_THRESHOLD:
            logger.info("token 超限，触发压缩")
            summarize(self.session)

    # ── 私有组装方法 ──────────────────────────────────────────────────

    def _make_system_prompt(self) -> dict:
        content = build_system_prompt(
            heartbeat_file=self.workspace.heartbeat_file,
            is_sub_agent=self.is_sub_agent,
            agent_file=self.workspace.agent_file,
            skills_dir=self.workspace.skills_dir,
            memory_file=self.workspace.memory_file,
        )
        return {"role": "system", "content": content}

    def _build_react_messages(self) -> list[dict]:
        return [self._make_system_prompt(), *self.session.messages]

    def _build_router_messages(self, route_instruction: str) -> list[dict]:
        messages = [self._make_system_prompt(), *self.session.messages]
        messages.append({"role": "user", "content": route_instruction})
        return messages

    def _build_replanner_messages(self) -> list[dict]:
        from qrclaw.graph.nodes.replanner import _REPLANNER_PROMPT
        ps = self._plan_state
        if ps is None:
            raise ValueError("plan_state 为空，无法构建 replanner prompt")

        past_text = "\n".join(
            step.to_context_prompt() for step in ps.past_steps
        ) or "（无）"

        remaining_text = "\n".join(
            f"- Step {s.id}: {s.description}" for s in ps.remaining
        ) or "（无剩余步骤）"

        prompt = (
            _REPLANNER_PROMPT
            .replace("{goal}", ps.goal)
            .replace("{past_steps}", past_text)
            .replace("{remaining_steps}", remaining_text)
        )
        return [{"role": "user", "content": prompt}]
