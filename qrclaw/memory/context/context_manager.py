"""
ContextManager —— 统一上下文管理中心

职责：
- 线程级单例，通过 get_context_manager() 全局获取，无需传参
- 持有主 session、workspace、plan_state
- 为不同角色（react / router / replanner） 组装 messages
- 线程安全地管理 plan 执行状态（加锁保护并发写入）
- 压缩判断集中在此，不散落在各节点
- System Prompt 缓存：失效后延迟重建

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
from qrclaw.memory.compression.compressor import summarize, summarize_messages
from qrclaw.memory.token_utils import count_messages_tokens
from qrclaw.memory.wiki.extraction.strategies import conversation_messages
from qrclaw.config import COMPRESS_THRESHOLD, _MODEL_MAX_TOKENS
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
1. 【用户意图为准】：只有用户明确说了"写入文件""保存""导出""生成文件""产出文档"时，goal 才可以包含"写入磁盘"。
   用户说"看看""分析""帮我查""汇报""讲一下"时，goal 只需要描述分析目标，绝对不画蛇添足加"写入磁盘"。
2. 【口头汇报也是交付】：分析、调研、代码审查类任务，最终用对话直接汇报结果就是完成。
   不要强制创建文件来存放本可以用几段话讲清楚的内容。

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
    """管理当前线程的会话、计划状态和系统提示缓存。"""

    def __init__(self, session: Session, workspace: Workspace, is_sub_agent: bool = False):
        self.session = session
        self.workspace = workspace
        self.is_sub_agent = is_sub_agent
        self._plan_state: PlanState | None = None
        self._lock = threading.Lock()  # 保护 plan_state 的并发写入

        # 系统提示缓存，以 None 表示需要重建
        self._cached_system_prompt: str | None = None
        self._extractor = None

    @property
    def extractor(self):
        """当前工作区的记忆提取器；检查点保存在会话中，可跨运行恢复。"""
        if self._extractor is None:
            from qrclaw.graph.nodes.memory_extraction import MemoryExtractionNode
            from qrclaw.memory.wiki import WikiMemory
            self._extractor = MemoryExtractionNode(WikiMemory.for_workspace(self.workspace.memory_dir))
        return self._extractor

    # ── Plan 状态管理（线程安全） ──────────────────────────────────────

    def set_plan(self, goal: str, steps: list, project_path: str = ""):
        """初始化 plan。"""
        with self._lock:
            self._plan_state = PlanState(goal=goal, remaining=list(steps), project_path=project_path)
        self.invalidate_cache()
        logger.info(f"plan 初始化：{goal}，项目路径：{project_path}，共 {len(steps)} 步")

    def set_wiki_context(self, wiki_context: str) -> None:
        """设置 Wiki 查询上下文（仅 plan 模式由 Router 触发，用于重建 System Prompt）。"""
        with self._lock:
            if self._plan_state is None:
                logger.debug("set_wiki_context: 非 plan 模式，跳过 Wiki 注入")
                return
            self._plan_state.wiki_context = wiki_context

        self.invalidate_cache()

        if wiki_context:
            logger.info(f"Wiki 上下文已缓存（{len(wiki_context)} 字符），将在下次构建 System Prompt 时注入")
        else:
            logger.debug("Wiki 上下文已清空，下次重建系统提示")

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
        self.invalidate_cache()

    @property
    def plan_state(self) -> PlanState | None:
        return self._plan_state

    # ── System Prompt 缓存管理 ─────────────────────

    def invalidate_cache(self) -> None:
        """
        标记 System Prompt 缓存为脏，需要重建。

        在以下情况调用：
        - 记忆被修改时
        - 用户主动要求
        """
        self._cached_system_prompt = None
        logger.debug("System Prompt 缓存已失效")

    def _get_system_prompt(self) -> str:
        """
        获取 System Prompt 内容。
        缓存为空时重建，否则返回缓存。
        """
        if self._cached_system_prompt is None:
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
            logger.info("System Prompt 构建完成")

        return self._cached_system_prompt

    # ── 消息组装 ──────────────────────────────────────────────────────

    def build_messages(self, role: str, tools: list[dict] | None = None, **kwargs) -> list[dict]:
        """每次请求前组装实际消息，按消息及工具定义检查预算。"""
        builders = {
            "react": self._build_react_messages,
            "router": self._build_router_messages,
            "replanner": self._build_replanner_messages,
        }
        if role not in builders:
            raise ValueError(f"未知 role: {role}")
        build = builders[role]
        messages = build(**kwargs)
        if count_messages_tokens(messages, tools) > COMPRESS_THRESHOLD:
            if role == "replanner":
                # 步骤战报独立压缩，保留目标、路径和重规划指令原文。
                history = [{"role": "assistant", "content": step.to_context_prompt()} for step in self._plan_state.past_steps]
                compacted = summarize_messages(history)
                messages = build(past_text="\n".join(m["content"] for m in compacted), **kwargs)
            else:
                summarize(self.session)
                messages = build(**kwargs)
        if count_messages_tokens(messages, tools) > int(_MODEL_MAX_TOKENS * 0.9):
            raise ValueError("上下文仍超出模型输入预算；原始近期消息已保留，请缩短输入或工具定义")
        return messages

    # ── 私有组装方法 ──────────────────────────────────────────────────

    def _build_react_messages(self) -> list[dict]:
        return [{"role": "system", "content": self._get_system_prompt()}, *self.session.messages]

    def _build_router_messages(self, route_instruction: str) -> list[dict]:
        """
        为 Router 构建消息。
        过滤掉工具调用和工具返回，只保留用户对话和系统提示。
        """
        filtered = conversation_messages(self.session.messages)
        messages = [{"role": "system", "content": _ROUTER_SYSTEM_PROMPT}, *filtered]
        messages.append({"role": "user", "content": route_instruction})
        return messages

    def _build_replanner_messages(self, replanner_instruction: str = "", past_text: str | None = None) -> list[dict]:
        from qrclaw.graph.nodes.replanner import _REPLANNER_PROMPT
        ps = self._plan_state
        if ps is None:
            raise ValueError("plan_state 为空，无法构建 replanner prompt")

        if past_text is None:
            past_text = "\n".join(step.to_context_prompt() for step in ps.past_steps) or "（无）"

        remaining_text = "\n".join(
            f"- Step {s.id}: {s.description}" for s in ps.remaining
        ) or "（无剩余步骤）"

        prompt = (
            _REPLANNER_PROMPT
            .replace("{goal}", ps.goal)
            .replace("{project_path}", ps.project_path or "（未指定，请从战报中推断）")
            .replace("{past_steps}", past_text)
            .replace("{remaining_steps}", remaining_text)
        )
        messages = [{"role": "user", "content": prompt}]
        if replanner_instruction:
            messages.append({"role": "user", "content": replanner_instruction})
        return messages
