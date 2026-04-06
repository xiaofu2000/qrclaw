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

# Router 专用系统提示词
_ROUTER_SYSTEM_PROMPT = """【系统指令】根据以上对话，判断最新一条用户消息是否需要制定执行计划，只返回 JSON，不要其他内容。

重要：你的输出必须是且仅是一个 JSON 对象，禁止输出任何自然语言、解释或 Markdown。不要回答用户的问题，只做路由判断。

【判断规则】
需要计划（route="plan"）的情况：
1. 任务含 3 个及以上明确步骤
2. 任务涉及多个文件、模块或方向，可以并行处理
3. 任务需要先探索环境再决定后续步骤
4. 任务明确要求分阶段完成

不需要计划（route="direct"）的情况：
1. 简单问答、解释、翻译
2. 单个文件操作
3. 单条命令执行
4. 闲聊

【输出格式】
必须先输出 "thought" 字段进行逻辑推理，再输出 route 结论。

【目标设定法则 (Definition of Done)】
当你提取用户的请求并生成 `goal`（终极目标）时，必须严格遵守以下验收标准：
1. 【实体交付优先】：如果用户的意图是生成代码、重构文件、撰写报告等【长文本产出】，你的 `goal` 必须明确要求"将最终结果写入磁盘"。
2. 【禁止口头完结】：绝不允许将"搜集完情报"或"得出结论"作为最终目标！必须落实到物理文件的修改或创建。

简单任务只返回：
{
  "thought": "简要分析用户的意图，说明为什么这是一个简单任务。",
  "route": "direct"
}

复杂任务返回（同时生成执行计划）：
{
  "thought": "分析任务的复杂度和包含的物理步骤，梳理出需要并行的模块和依赖关系。分析当前用户的目标路径。强制自我审查：目标路径是否是一个全新的目录？如果是，必须要写绝对路径！",
  "route": "plan",
  "project_path": "从对话上下文中推断出的项目根目录绝对路径，如 /Users/xxx/myproject",
  "goal": "任务目标的简短描述",
  "steps": [
    {"id": 1, "description": "步骤描述，如果有路径必须是绝对路径", "depends_on": []},
    {"id": 2, "description": "步骤描述，如果有路径必须是绝对路径", "depends_on": [1]},
    {"id": 3, "description": "步骤描述，如果有路径必须是绝对路径", "depends_on": []},
    {"id": 4, "description": "步骤描述，如果有路径必须是绝对路径", "depends_on": [2, 3]}
  ]
}

【规划规则】
- 每个步骤具体、可执行，一步只做一件事
- depends_on 填前置步骤 id，没有依赖填空数组
- 无依赖的步骤会被并行执行，有依赖的步骤串行等待
- 【动态规划视野（战争迷雾）】：你不必强制生成全部步骤。如果缺乏上下文（如未知目录），只需生成 1~2 个探测步骤（如 ls, find），绝不要凭空猜测后续！如果情报充足，请尽可能多地规划可并行的独立步骤。
- 需要汇总或综合分析前置步骤结果的步骤，必须在 depends_on 中列出所有它依赖的步骤 id
- 【上下文隔离与防幻觉原则】：执行步骤的子 agent 看不到对话历史，因此对于用户明确提供的已知信息（目录、参数等），必须直接写入描述中实现自包含。
- 【严禁瞎编具体细节】：对于需要前置步骤（depends_on）动态搜索才能得知的未知信息（如具体文件名），绝对禁止在描述中盲目猜测或举例（例如禁止写"如 session.py"）。必须指示子 agent："使用前置步骤 [id] 传递过来的结果进行处理"。
- 步骤描述要足够详细，相当于给一个全新的 agent 下达完整任务指令：包括做什么、怎么做、目标是什么、输出什么"""


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
        return [{"role": "system", "content": self._get_system_prompt()}, *self.session.messages]

    def _build_router_messages(self, route_instruction: str) -> list[dict]:
        """
        为 Router 构建消息。
        过滤掉工具调用和工具返回，只保留用户对话和系统提示。
        """
        filtered = self._filter_for_router(self.session.messages)
        messages = [{"role": "system", "content": _ROUTER_SYSTEM_PROMPT}, *filtered]
        messages.append({"role": "user", "content": route_instruction})
        return messages

    def _filter_for_router(self, messages: list[dict]) -> list[dict]:
        """
        过滤消息，只保留用户输入和 assistant 最终纯文字回复。

        过滤掉的内容：
        - role=tool 的消息（工具返回）
        - assistant 消息中带有 tool_calls 的消息（含中间推理 content 一并丢弃）

        保留的内容：
        - role=user 的消息（用户输入）
        - assistant 消息中没有 tool_calls 的消息（最终纯文字回复）
        """
        filtered = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role")

            if role == "user":
                # 用户消息直接保留
                filtered.append(msg)

            elif role == "assistant":
                # 有 tool_calls 的消息（包含中间推理）直接丢弃
                if not msg.get("tool_calls"):
                    filtered.append(msg)

            # role=tool 的消息直接跳过

            i += 1

        return filtered

    def _build_replanner_messages(self, replanner_instruction: str = "", **kwargs) -> list[dict]:
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
        messages = [{"role": "user", "content": prompt}]
        if replanner_instruction:
            messages.append({"role": "user", "content": replanner_instruction})
        return messages
