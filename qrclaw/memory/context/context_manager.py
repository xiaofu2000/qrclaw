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
import threading
from dataclasses import dataclass, field
from qrclaw.memory.context.session import Session
from qrclaw.memory.compression.compressor import summarize
from qrclaw.config import COMPRESS_THRESHOLD
from qrclaw.workspace import Workspace
from qrclaw.prompt import build_system_prompt
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.context_manager")

# Router 专用系统提示
_ROUTER_SYSTEM_PROMPT = """你是一个任务路由器。

根据用户的输入，判断应该使用哪种执行方式：

1. direct（直接执行）：适合简单、明确的任务
   - 单步骤操作（如读文件、写文件、运行命令）
   - 明确的问答
   - 不需要多步骤或并行处理

2. plan（计划执行）：适合复杂、需要多步骤的任务
   - 需要多步骤才能完成
   - 需要探索未知结构（目录、代码库）
   - 需要并行处理多个独立子任务
   - 任务目标不明确，需要拆解

输出格式（必须是有效的 JSON）：
{
    "route": "direct" 或 "plan",
    "goal": "任务目标（plan 模式必填）",
    "project_path": "项目根目录绝对路径（如有）",
    "steps": [
        {"id": "1", "description": "步骤描述", "depends_on": []},
        {"id": "2", "description": "步骤描述", "depends_on": ["1"]}
    ]
}

注意：
- route 为 direct 时，goal 和 steps 可以省略或为空
- depends_on 为空数组表示无依赖，可并行执行
- project_path 填写推测的项目根目录路径
"""


@dataclass
class PlanState:
    """当前正在执行的计划状态，由 ContextManager 直接持有。"""
    goal: str
    past_steps: list = field(default_factory=list)  # list[StepResult]
    remaining: list = field(default_factory=list)   # list[PlanStep]
    project_path: str = ""  # 项目根目录绝对路径，由 LLM 从对话中推断


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
            self._cached_system_prompt = build_system_prompt(
                heartbeat_file=self.workspace.heartbeat_file,
                is_sub_agent=self.is_sub_agent,
                agent_file=self.workspace.agent_file,
                skills_dir=self.workspace.skills_dir,
                memory_dir=self.workspace.memory_dir,
            )
            self._dirty = False
            logger.info("System Prompt 构建完成")

        return self._cached_system_prompt

    # ── 消息组装 ──────────────────────────────────────────────────────

    def build_messages(self, role: str, **kwargs) -> list[dict]:
        """
        为不同角色组装 messages。

        role:
          "react"      → [system] + session.messages
          "router"     → [system: router专用] + session.messages + [route_instruction]
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
        return [{"role": "system", "content": self._get_system_prompt()}, *self.session.messages]

    def _build_router_messages(self, route_instruction: str) -> list[dict]:
        # Router 使用专用的系统提示
        messages = [{"role": "system", "content": _ROUTER_SYSTEM_PROMPT}, *self.session.messages]
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
