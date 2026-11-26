"""双模式模型驱动层：提供离线高保真 ScriptedEvalProvider 与在线真实模型遥测适配器。"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import re
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

from qrclaw.llm_service import LLMService
from qrclaw.providers.base import LLMProvider, LLMResponse, ToolCall
from qrclaw.providers.litellm_provider import LiteLLMProvider

SOURCE_HEADER_RE = re.compile(
    r"\[来源 (?P<id>[a-f0-9]+)；(?P<role>user|assistant)；(?P<kind>[a-z_]+)\]\n(?P<body>.*?)(?=\n\n\[来源 [a-f0-9]+；|\Z)",
    re.DOTALL,
)


@dataclass
class EvalTelemetry:
    """采集单个评测用例运行期间的 LLM 调用次数、Token 消耗与实际提示词快照。"""

    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    router_calls: int = 0
    captured_prompts: list[str] = field(default_factory=list)

    def record(self, messages: list[dict], response: LLMResponse) -> None:
        self.llm_calls += 1
        rendered = "\n".join(str(m.get("content", "")) for m in messages if m.get("content"))
        self.captured_prompts.append(rendered)
        p_tok = response.prompt_tokens or max(1, len(rendered) // 3)
        c_tok = response.completion_tokens or max(1, len(response.content or "") // 3)
        t_tok = response.total_tokens or (p_tok + c_tok)
        self.prompt_tokens += p_tok
        self.completion_tokens += c_tok
        self.total_tokens += t_tok


def parse_prompt_sources(prompt: str) -> list[dict[str, str]]:
    """解析记忆提取提示词中由 WikiProvenance 注入的真实来源块。"""
    return [
        {
            "id": match.group("id"),
            "role": match.group("role"),
            "kind": match.group("kind"),
            "body": match.group("body").strip(),
        }
        for match in SOURCE_HEADER_RE.finditer(prompt)
    ]


class ScriptedEvalProvider(LiteLLMProvider, LLMProvider):
    """
    离线高保真脚本化评测 Provider。

    同时继承 LiteLLMProvider 与 LLMProvider：
    - 完整兼容 LLMService.chat / LLMService.structured（instructor Mode.MD_JSON）
    - 完整兼容 RouterNode / WikiPageSelector / Compressor / ReactLoop 工具调用协议
    - 真实解析传入提示词中的动态来源 ID、旧页面正文与工具返回结果，杜绝假数据旁路
    """

    def __init__(
        self,
        scenario: str,
        telemetry: EvalTelemetry,
        context_state: dict[str, Any] | None = None,
    ):
        # 不调用依赖外部环境变量的联网初始化，保持纯内存确定性配置
        self._api_key = "offline-eval-key"
        self._base_url = None
        self._proxy_url = None
        self._model = "eval/scripted-offline"
        self.scenario = scenario
        self.telemetry = telemetry
        self.state = context_state if context_state is not None else {}
        self._react_step = 0

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        on_before_chat = self.state.get("on_before_chat")
        if callable(on_before_chat):
            on_before_chat(messages)

        full_text = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("content"))

        # 1. 识别 Wiki 记忆提取分析提示词（EXTRACTION_PROMPT_TEMPLATE）
        if "### 现有 Wiki 索引" in full_text and "### 对话历史" in full_text:
            response = self._handle_memory_extraction(full_text)
        # 2. 识别 Wiki 页面新旧合并提示词（CONSOLIDATION_PROMPT_TEMPLATE）
        elif "## 现有页面内容" in full_text and "## 新内容片段" in full_text:
            response = self._handle_memory_consolidation(full_text)
        # 3. 识别上下文递归摘要提示词（SUMMARIZE_PROMPT）
        elif "【用户信息】" in full_text and "【已完成任务】" in full_text and "对话内容：" in full_text:
            response = self._handle_context_summary(full_text)
        # 4. 识别 Wiki 智能目录选页提示词（SELECTION_PROMPT_TEMPLATE）
        elif "selected" in full_text and "relevance" in full_text and ("Wiki" in full_text or "目录" in full_text or "页面" in full_text):
            response = self._handle_wiki_selection(full_text)
        # 5. 识别 Router 路由提示词（ROUTER_SYSTEM_PROMPT）
        elif 'route="plan"' in full_text or "请根据以上对话判断并输出 JSON" in full_text:
            self.telemetry.router_calls += 1
            response = self._handle_router(messages)
        # 6. ReAct 工具循环对话
        elif tools is not None:
            response = self._handle_react(messages, tools)
        else:
            response = LLMResponse(content="离线评测默认响应。")

        if on_delta is not None and response.content:
            on_delta(response.content)
        self.telemetry.record(messages, response)
        return response

    # ── 各真实子系统协议处理器 ────────────────────────────────────────────────

    def _handle_memory_extraction(self, prompt: str) -> LLMResponse:
        sources = parse_prompt_sources(prompt)
        user_ids = [s["id"] for s in sources if s["role"] == "user"]
        assistant_ids = [s["id"] for s in sources if s["role"] == "assistant"]

        if self.scenario == "extract_user_preferences":
            payload = {
                "needs_update": True,
                "pages": [
                    {
                        "action": "create",
                        "name": "编码与回复偏好",
                        "description": "用户的中文回复约定与 Python 编码测试规范",
                        "tags": ["偏好", "Python", "测试"],
                        "related": [],
                        "source_ids": user_ids,
                        "content": (
                            "# 编码与回复偏好\n\n"
                            "- **回复语言**：所有技术回复一律使用简体中文，结论先行。\n"
                            "- **Python 规范**：函数签名必须添加完整类型注解，路径操作统一使用 `pathlib.Path`（禁用 `os.path`）。\n"
                            "- **测试风格**：默认使用 `pytest` 函数式测试风格。\n"
                        ),
                    }
                ],
            }
        elif self.scenario == "consolidate_override_config":
            payload = {
                "needs_update": True,
                "pages": [
                    {
                        "action": "append",
                        "name": "项目服务配置",
                        "description": "FastAPI 网关框架、8765 端口与数据库日志约定",
                        "tags": ["配置", "后端", "FastAPI"],
                        "related": [],
                        "source_ids": user_ids,
                        "content": (
                            "后端 Web 框架已从 Flask 迁移至 FastAPI（Flask 已下线）；"
                            "本地监听端口由 8000 变更为 8765；PostgreSQL 16 与结构化 JSON 日志保持不变。"
                        ),
                    }
                ],
            }
        elif self.scenario == "skip_ephemeral_chat":
            payload = {
                "needs_update": False,
                "pages": [],
            }
        elif self.scenario == "reject_assistant_speculation":
            # 若开启了强制探测助手单源拦截开关，则模拟违规模型仅引用 assistant 来源 ID，检验 ExtractionRunner 拦截器
            if self.state.get("simulate_rogue_assistant_source") and assistant_ids:
                payload = {
                    "needs_update": True,
                    "pages": [
                        {
                            "action": "create",
                            "name": "猜测的用户画像",
                            "description": "未经用户确认的助手臆测",
                            "tags": ["猜测"],
                            "related": [],
                            "source_ids": assistant_ids,
                            "content": "用户是高频量化交易 Rust 内核工程师，偏好 Neovim 和 Dvorak 键盘布局。",
                        }
                    ],
                }
            else:
                payload = {"needs_update": False, "pages": []}
        elif self.scenario == "filter_tool_and_summary_noise":
            payload = {
                "needs_update": True,
                "pages": [
                    {
                        "action": "create",
                        "name": "线上发版纪律",
                        "description": "CI 冻结依赖安装、健康检查切流与回滚镜像保留规范",
                        "tags": ["发版", "CI"],
                        "related": [],
                        "source_ids": user_ids,
                        "content": (
                            "# 线上发版纪律\n\n"
                            "- CI 构建统一执行 `uv sync --frozen`。\n"
                            "- 服务启动后须等待 `/health` 接口返回 `200` 方可切流。\n"
                            "- 回滚镜像至少保留最近 5 个正式版本标签。\n"
                        ),
                    }
                ],
            }
        elif self.scenario == "explicit_write_and_dedup":
            payload = {
                "needs_update": True,
                "pages": [
                    {
                        "action": "create",
                        "name": "前端包管理规范",
                        "description": "web 目录仅允许使用 npm 管理依赖与脚本",
                        "tags": ["前端", "npm"],
                        "related": [],
                        "source_ids": user_ids,
                        "content": "# 前端包管理规范\n\n前端目录 `web/` 统一使用 `npm ci` 与 `npm run`，严禁混用 `pnpm` 或 `yarn`。\n",
                    }
                ],
            }
        elif self.scenario == "preserve_unextracted_before_compression":
            payload = {
                "needs_update": True,
                "pages": [
                    {
                        "action": "create",
                        "name": "数据库迁移脚本规范",
                        "description": "迁移脚本存放目录与四位序号不可变约定",
                        "tags": ["数据库", "规范"],
                        "related": [],
                        "source_ids": user_ids,
                        "content": (
                            "# 数据库迁移脚本规范\n\n"
                            "- 存放路径：`db/migrations/versions/`\n"
                            "- 命名规则：四位序号加下划线前缀（如 `0001_init.sql`）\n"
                            "- 变更原则：严禁修改已发布的旧迁移文件，表结构变更必须新增文件。\n"
                        ),
                    }
                ],
            }
        else:
            payload = {"needs_update": False, "pages": []}

        return LLMResponse(content=f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```")

    def _handle_memory_consolidation(self, prompt: str) -> LLMResponse:
        if self.scenario == "consolidate_override_config":
            payload = {
                "content": (
                    "# 项目服务配置\n\n"
                    "- Web 框架：FastAPI\n"
                    "- 本地监听端口：8765\n"
                    "- 主数据库：PostgreSQL 16\n"
                    "- 日志输出格式：结构化 JSON\n"
                ),
                "description": "FastAPI 网关框架、8765 监听端口与数据库日志约定",
                "tags": ["配置", "后端", "FastAPI"],
                "related": [],
            }
        else:
            payload = {
                "content": "# 已整理页面\n\n保留有效配置事实。",
                "description": "整理后的记忆页面",
                "tags": ["记忆"],
                "related": [],
            }
        return LLMResponse(content=f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```")

    def _handle_context_summary(self, prompt: str) -> LLMResponse:
        history_part = prompt.split("对话内容：", 1)[-1]
        if self.scenario == "summary_fact_retention_score":
            summary = (
                "【用户信息】平台安全组后端维护者，要求全程使用精简中文汇报进度。\n"
                "【已完成任务】已修复 `qrclaw/server/routes.py` 中的 SSE 断线重连序号问题，并运行 `pytest -q` 回归测试全部通过。\n"
                "【关键结论】本地 API 服务默认仅监听 `127.0.0.1:8765`；若需绑定非本机地址，必须配置环境变量 `QRCLAW_API_TOKEN` 强制鉴权。\n"
                "【待处理事项】1. 编写 `docs/api-security.md` 安全说明文档；2. 补充并发槽位上限 `MAX_CONCURRENT_RUNS` 的压力测试。"
            )
        elif self.scenario == "recursive_compress_and_tool_pair":
            has_old = "[SUMMARY]" in history_part
            summary = (
                "【用户信息】全栈开发工程师（macOS 环境）。\n"
                f"【已完成任务】{'合并历史摘要：已完成仓库初始化与依赖安装；' if has_old else ''}已排查网关日志与连接池参数 `pool_recycle: 30`。\n"
                "【关键结论】登录接口偶发 502 系上游空闲回收短于负载均衡器 `keepalive_idle: 60` 所致。\n"
                "【待处理事项】按用户要求将连接池超时参数调整为 120 秒。"
            )
        else:
            summary = (
                "【用户信息】项目研发人员。\n"
                "【已完成任务】已完成早期规范确认与背景梳理。\n"
                "【关键结论】遵循既定目录与接口契约。\n"
                "【待处理事项】继续处理当前轮次的最新任务。"
            )
        return LLMResponse(content=summary)

    def _handle_wiki_selection(self, prompt: str) -> LLMResponse:
        candidates = re.findall(r"^- ([^：\n]+)：(.*)$", prompt, flags=re.MULTILINE)
        selected = []
        if "令牌" in prompt or "鉴权" in prompt or "QRCLAW_API_TOKEN" in prompt:
            for name, _desc in candidates:
                if "鉴权" in name:
                    selected.append({"name": name.strip(), "reason": "直接说明远程访问令牌鉴权规则", "relevance": 0.95})
        if not selected and candidates:
            selected.append({"name": candidates[0][0].strip(), "reason": "目录候选匹配", "relevance": 0.75})
        payload = {"selected": selected, "reasoning": "根据问题语义匹配最相关的知识页"}
        return LLMResponse(content=f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```")

    def _handle_router(self, messages: list[dict]) -> LLMResponse:
        user_msgs = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
        latest_user = user_msgs[-2] if len(user_msgs) >= 2 else (user_msgs[-1] if user_msgs else "")

        if "多阶段" in latest_user or "第一阶段" in latest_user or "并行检查" in latest_user:
            path_match = re.search(r"(/[^\s，。；]+)", latest_user)
            project_path = path_match.group(1) if path_match else "/tmp/eval-project"
            payload = {
                "thought": "该任务明确包含互不依赖的前后端并行检查阶段及后续汇总阶段，满足多文件并行与分阶段规则，应路由为 plan。",
                "route": "plan",
                "project_path": project_path,
                "goal": "并行检查后端接口超时与前端请求重试配置，并汇总对齐建议",
                "steps": [
                    {
                        "id": 1,
                        "description": f"读取并检查 {project_path}/backend/api.py 中的接口超时配置参数",
                        "depends_on": [],
                    },
                    {
                        "id": 2,
                        "description": f"读取并检查 {project_path}/frontend/config.ts 中的请求重试配置参数",
                        "depends_on": [],
                    },
                    {
                        "id": 3,
                        "description": "使用前置步骤 [1, 2] 传递过来的检查结果进行对比，汇总前后端超时与重试对齐建议",
                        "depends_on": [1, 2],
                    },
                ],
            }
        else:
            payload = {
                "thought": "用户仅请求用两三句话解释标准库装饰器概念，属于单轮简单问答，直接走 direct。",
                "route": "direct",
            }
        return LLMResponse(content=f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```")

    def _handle_react(self, messages: list[dict], _tools: list[dict]) -> LLMResponse:
        self._react_step += 1
        step = self._react_step

        if self.scenario == "plan_disabled_skips_router":
            return LLMResponse(
                content=(
                    "按三步估算 10 万日活通知系统的 Redis 内存开销：\n"
                    "1. 单用户未读索引与元数据约 512 Bytes；\n"
                    "2. 10 万活跃用户热数据约 50 MB，计入通知正文缓存与 Hash 结构开销约 200~350 MB；\n"
                    "3. 预留峰值与碎片安全冗余后，建议分配 **1 GB Redis 内存**。"
                ),
                finish_reason="stop",
            )

        if self.scenario == "react_read_patch_verify":
            target_path = self.state["file_path"]
            if step == 1:
                return LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call_eval_read_1",
                            name="read_file",
                            arguments=json.dumps({"path": target_path}),
                        )
                    ],
                )
            if step == 2:
                return LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call_eval_patch_1",
                            name="patch",
                            arguments=json.dumps({
                                "mode": "replace",
                                "path": target_path,
                                "old_string": "DEFAULT_TIMEOUT = 10\nRETRY_ENABLED = False",
                                "new_string": "DEFAULT_TIMEOUT = 45\nRETRY_ENABLED = True",
                            }),
                        )
                    ],
                )
            if step == 3:
                return LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call_eval_read_2",
                            name="read_file",
                            arguments=json.dumps({"path": target_path}),
                        )
                    ],
                )
            return LLMResponse(
                content="已完成修改并复核确认：`DEFAULT_TIMEOUT` 已更新为 `45`，`RETRY_ENABLED` 已更新为 `True`。",
                finish_reason="stop",
            )

        if self.scenario == "tool_error_self_healing":
            target_path = self.state["file_path"]
            if step == 1:
                # 第 1 步故意传错旧端口号，触发 patch 未找到匹配文本报错
                return LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call_heal_patch_err",
                            name="patch",
                            arguments=json.dumps({
                                "mode": "replace",
                                "path": target_path,
                                "old_string": "port: 9999",
                                "new_string": "port: 9090",
                            }),
                        )
                    ],
                )
            if step == 2:
                # 第 2 步读取真实文件内容进行自愈诊断
                return LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call_heal_read",
                            name="read_file",
                            arguments=json.dumps({"path": target_path}),
                        )
                    ],
                )
            if step == 3:
                # 第 3 步使用核实后的真实参数成功替换
                return LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call_heal_patch_ok",
                            name="patch",
                            arguments=json.dumps({
                                "mode": "replace",
                                "path": target_path,
                                "old_string": "port: 8080",
                                "new_string": "port: 9090",
                            }),
                        )
                    ],
                )
            return LLMResponse(
                content="刚才首次替换时原端口不匹配，我已重新读取文件核实并将 `port: 8080` 更新为 `port: 9090`（`host: 127.0.0.1` 与 `workers: 4` 保持不变）。",
                finish_reason="stop",
            )

        if self.scenario == "workspace_security_boundary":
            if step == 1:
                return LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call_sec_outside_read",
                            name="read_file",
                            arguments=json.dumps({"path": "../outside_credentials.env"}),
                        ),
                        ToolCall(
                            id="call_sec_sensitive_write",
                            name="write_file",
                            arguments=json.dumps({
                                "path": "/etc/qrclaw_eval_forbidden.conf",
                                "content": "test=1",
                            }),
                        ),
                    ],
                )
            if step == 2:
                return LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            id="call_sec_inside_read",
                            name="read_file",
                            arguments=json.dumps({"path": "/workspace/allowed_note.txt"}),
                        )
                    ],
                )
            return LLMResponse(
                content=(
                    "安全边界拦截报告：\n"
                    "1. 读取 `../outside_credentials.env` 被沙箱路径边界拒绝（超出工作区）；\n"
                    "2. 写入 `/etc/qrclaw_eval_forbidden.conf` 被敏感系统路径保护拒绝；\n"
                    "3. 已正常读取工作区内 `/workspace/allowed_note.txt`（版本 0.9.2）。"
                ),
                finish_reason="stop",
            )

        return LLMResponse(content="ReAct 任务执行完毕。", finish_reason="stop")


class _OfflineInstructorClient:
    """将 RouterNode 的 client.chat.completions.create 调用桥接到真实 instructor MD_JSON 解析器。"""

    def __init__(self, llm_service: LLMService):
        import instructor

        patched_create = instructor.patch(
            create=llm_service.create_openai_like,
            mode=instructor.Mode.MD_JSON,
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=patched_create))


def ensure_live_model_ready() -> str:
    """在 --mode live 启动前检查当前配置的真实模型与凭证是否就绪。"""
    from qrclaw.providers import provider

    model_name = str(getattr(provider, "_model", "") or "")
    api_key = str(getattr(provider, "_api_key", "") or "")
    if not model_name:
        raise RuntimeError(
            "未检测到有效的模型配置。请在 ~/.qrclaw/config.yaml 的 llm 段或环境变量 LITELLM_MODEL 中配置模型后再运行 --mode live。"
        )
    if not api_key and not model_name.startswith(("ollama/", "vertex_ai/")):
        raise RuntimeError(
            f"当前模型为 {model_name!r}，但未配置 API Key。请在 ~/.qrclaw/config.yaml 的 llm.api_key 或环境变量 LITELLM_API_KEY 中填写密钥，或改用 --mode offline 运行离线回归评测。"
        )
    return model_name


@contextmanager
def eval_model_context(
    mode: str,
    scenario: str,
    context_state: dict[str, Any] | None = None,
):
    """
    统一管理 offline / live 两种模式的模型注入与遥测指标收集。

    Yields:
        tuple[EvalTelemetry, str]: (遥测统计对象, 模型名称)
    """
    telemetry = EvalTelemetry()

    if mode == "offline":
        scripted = ScriptedEvalProvider(scenario=scenario, telemetry=telemetry, context_state=context_state)
        offline_service = LLMService(scripted)
        with (
            patch("qrclaw.providers.provider", scripted),
            patch("qrclaw.llm_service.get_llm_service", return_value=offline_service),
            patch(
                "qrclaw.graph.nodes.router._get_instructor_client",
                side_effect=lambda: _OfflineInstructorClient(offline_service),
            ),
        ):
            yield telemetry, scripted._model
        return

    # live 模式：使用用户真实配置的 get_llm_service() 与 provider，仅外挂无侵入遥测统计
    from qrclaw.providers import provider as live_provider
    import qrclaw.providers.litellm_provider as litellm_mod
    import qrclaw.graph.nodes.router as router_mod

    model_name = ensure_live_model_ready()
    original_chat = live_provider.chat
    original_router_client = router_mod._get_instructor_client
    on_before_chat = (context_state or {}).get("on_before_chat")

    def tracked_chat(messages: list[dict], *args: Any, **kwargs: Any) -> LLMResponse:
        if callable(on_before_chat):
            on_before_chat(messages)
        resp = original_chat(messages, *args, **kwargs)
        telemetry.record(messages, resp)
        return resp

    def tracked_router_client():
        telemetry.router_calls += 1
        client = original_router_client()
        orig_create = client.chat.completions.create

        def wrapped_create(**kwargs: Any):
            msgs = kwargs.get("messages", [])
            result, raw_completion = orig_create(**kwargs) if kwargs.get("include_raw") else (orig_create(**kwargs), None)
            usage = getattr(raw_completion, "usage", None)
            est_prompt = getattr(usage, "prompt_tokens", 0) or max(1, sum(len(str(m.get("content", ""))) for m in msgs) // 3)
            telemetry.llm_calls += 1
            telemetry.prompt_tokens += est_prompt
            telemetry.completion_tokens += 60
            telemetry.total_tokens += est_prompt + 60
            return result

        client.chat.completions.create = wrapped_create
        return client

    with (
        patch.object(live_provider, "chat", side_effect=tracked_chat),
        patch.object(router_mod, "_get_instructor_client", side_effect=tracked_router_client),
        patch.object(litellm_mod, "logger", litellm_mod.logger),
    ):
        yield telemetry, model_name
