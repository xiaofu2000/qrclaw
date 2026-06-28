"""
ContextManager —— 统一上下文管理中心

职责：
- 线程级单例，通过 get_context_manager() 全局获取，无需传参
- 持有主 session、workspace、plan_state
- 为不同角色（react / router / replanner） 组装 messages
- 线程安全地管理 plan 执行状态（加锁保护并发写入）
- 压缩判断集中在此，不散落在各节点
- System Prompt 缓存：使用 Dirty Flag 模式，延迟更新

用法：
    # 主 agent 初始化时
    init_context_manager(session, workspace, is_sub_agent=False)

    # 任意地方取
    ctx = get_context_manager()
    messages = ctx.build_messages("react")
"""
from dataclasses import dataclass, field
import threading
from qrclaw.memory.context.session import Session
from qrclaw.memory.compression.compressor import summarize
from qrclaw.config import COMPRESS_THRESHOLD
from qrclaw.workspace import Workspace
from qrclaw.prompt_builder import build_system_prompt
from qrclaw.memory.context.message_builder import (
    build_react_messages,
    build_router_messages,
    build_replanner_messages,
)
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.context_manager")


@dataclass
class PlanState:
    """当前正在执行的计划状态，由 ContextManager 直接持有。"""
    goal: str
    past_steps: list = field(default_factory=list)  # list[StepResult]
    remaining: list = field(default_factory=list)   # list[PlanStep]
    project_path: str = ""  # 项目根目录绝对路径，由 LLM 从对话中推断
    wiki_context: str = ""   # Wiki 记忆上下文（通过 Router 触发 LLM 精排注入）


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

        # System Prompt 缓存（Dirty Flag 模式）
        self._cached_system_prompt: str | None = None
        self._dirty: bool = True  # 默认脏，首次使用时构建

    # ── 消息管理 ──────────────────────────────────────────────────────

    def add(self, message: dict):
        self.session.add(message)

    # ── Plan 状态管理（线程安全） ──────────────────────────────────────

    def set_plan(self, goal: str, steps: list, project_path: str = ""):
        """初始化 plan。"""
        with self._lock:
            self._plan_state = PlanState(goal=goal, remaining=list(steps), project_path=project_path)
        logger.info(f"plan 初始化：{goal}，项目路径：{project_path}，共 {len(steps)} 步")

    def set_wiki_context(self, wiki_context: str) -> None:
        """设置 Wiki 查询上下文（仅 plan 模式由 Router 触发，用于重建 System Prompt）。"""
        with self._lock:
            if self._plan_state is None:
                logger.debug("set_wiki_context: 非 plan 模式，跳过 Wiki 注入")
                return
            self._plan_state.wiki_context = wiki_context

        if wiki_context:
            logger.info(f"Wiki 上下文已缓存（{len(wiki_context)} 字符），将在下次构建 System Prompt 时注入")
            self.invalidate_cache()
        else:
            logger.debug("Wiki 查询无结果，跳过 System Prompt 重建")

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

    # ── System Prompt 缓存管理（Dirty Flag 模式） ─────────────────────

    def invalidate_cache(self) -> None:
        """
        标记 System Prompt 缓存为脏，需要重建。

        在以下情况调用：
        - 记忆被修改时（通过 _invalidate_context_manager_cache）
        - 压缩后
        - 用户主动要求
        """
        self._dirty = True
        self._cached_system_prompt = None
        logger.debug("System Prompt 缓存已失效")

    def _get_system_prompt(self) -> str:
        """
        获取 System Prompt 内容。
        使用 Dirty Flag 模式：脏时重建，否则返回缓存。
        """
        if self._dirty or self._cached_system_prompt is None:
            logger.debug("重建 System Prompt（缓存失效）")
            # 从 plan_state 获取 wiki_context
            wiki_context = ""
            with self._lock:
                if self._plan_state is not None:
                    wiki_context = self._plan_state.wiki_context
            self._cached_system_prompt = build_system_prompt(
                heartbeat_file=self.workspace.heartbeat_file,
                is_sub_agent=self.is_sub_agent,
                agent_file=self.workspace.agent_file,
                skills_dir=self.workspace.skills_dir,
                memory_dir=self.workspace.memory_dir,
                wiki_context=wiki_context,
            )
            self._dirty = False
            logger.info("System Prompt 构建完成")

        return self._cached_system_prompt

    # ── 消息组装 ──────────────────────────────────────────────────────

    def build_messages(self, role: str, **kwargs) -> list[dict]:
        """
        为不同角色组装 messages。

        role:
          "react"      → [system] + session.messages（完整）
          "router"     → [system] + 用户对话（过滤掉工具调用）
          "replanner"  → [user: replanner_prompt]（从 plan_state 自动构建）

        kwargs:
          route_instruction (str): role=router 时必传
        """
        if role == "react":
            return self._build_react_messages()
        elif role == "router":
            return self._build_router_messages(kwargs["route_instruction"])
        elif role == "replanner":
            return self._build_replanner_messages(**kwargs)
        else:
            raise ValueError(f"未知 role: {role}")

    def compress_if_needed(self):
        """检查 token 数，超限则压缩。"""
        # 使用 session.prompt_tokens 进行估算
        from qrclaw.memory.token_utils import count_text_tokens

        # 估算总 token 数：session 消息 + system prompt
        system_prompt_tokens = count_text_tokens(self._get_system_prompt())
        total_tokens = self.session.prompt_tokens + system_prompt_tokens

        if total_tokens > COMPRESS_THRESHOLD:
            logger.info(f"token 超限（估算 {total_tokens}），触发压缩")
            summarize(self.session)
            # 压缩后刷新缓存
            self.invalidate_cache()

    # ── 私有组装方法 ──────────────────────────────────────────────────

    def _build_react_messages(self) -> list[dict]:
        return build_react_messages(self._get_system_prompt(), self.session.messages)

    def _build_router_messages(self, route_instruction: str) -> list[dict]:
        return build_router_messages(self.session.messages, route_instruction)

    def _build_replanner_messages(self, replanner_instruction: str = "", **kwargs) -> list[dict]:
        ps = self._plan_state
        if ps is None:
            raise ValueError("plan_state 为空，无法构建 replanner prompt")

        past_text = "\n".join(
            step.to_context_prompt() for step in ps.past_steps
        ) or "（无）"

        remaining_text = "\n".join(
            f"- Step {s.id}: {s.description}" for s in ps.remaining
        ) or "（无剩余步骤）"
        return build_replanner_messages(
            goal=ps.goal,
            project_path=ps.project_path,
            past_steps_text=past_text,
            remaining_steps_text=remaining_text,
            replanner_instruction=replanner_instruction,
        )
