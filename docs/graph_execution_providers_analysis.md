# QRClaw graph / execution / providers 三模块源码分析

> 分析对象：`/Users/admin/Documents/agent/qrclaw/qrclaw/graph`、`/Users/admin/Documents/agent/qrclaw/qrclaw/execution`、`/Users/admin/Documents/agent/qrclaw/qrclaw/providers`（共 20 个 .py 源码文件）
> 覆盖重点：runner/executor/react_loop/plan_executor/replanner/router/tool_runner/memory_extraction/message_codec/wiki_query；context/models/projector/service/store；base/litellm_provider

---

## 0. 三模块总览

| 模块 | 行数规模 | 角色 | 核心定位 |
|---|---|---|---|
| `graph/` | ~1200 行（9 节点 + runner + executor） | **执行引擎** | 图结构编排：路由→ReAct/计划执行→重规划→记忆提取 |
| `execution/` | ~700 行（5 文件） | **运行协议层** | 事件驱动运行态：取消/授权/事件/快照/持久化（HTTP+WebSocket 后端协议） |
| `providers/` | ~350 行（3 文件） | **LLM 抽象层** | 统一 LLM 调用接口（LiteLLM 桥接） |

**依赖方向（严格单向）**：
```
server/cli → execution/service → agent.run() → graph/runner → graph/nodes → tools + memory + llm_service → providers
```
- `execution` 不依赖 `graph`（通过 `agent_runner` 回调注入，解耦）
- `graph` 不直接依赖 `providers`（通过 `llm_service.get_llm_service()` 间接调用，仅 router/replanner 用 `is_provider_type(LiteLLMProvider)` 做能力探测）
- `graph` 依赖 `execution.context`（`RunCancelled`、`ExecutionContext` 类型）

---

## 1. graph/ —— 图执行引擎

### 1.1 graph/runner.py —— 图的入口 `GraphRunner`

**职责**：节点编排 + 条件边路由。CLI/子 agent/心跳/MCP 均复用 `GraphRunner.run()`。

**关键类**：`GraphRunner`（组合 RouterNode、ReactLoopNode、PlanExecutorNode 三个节点，无图数据结构，是**手写 if/else 条件边**）。

**`run()` 参数**：`user_input, session, console, workspace, auto_confirm, is_sub_agent, run_sub_agent_fn, execution_context`。

**路由逻辑（条件边）**：
1. `is_sub_agent=True` → **跳过路由**，直接进 ReactLoop（子 agent 无计划能力）
2. 否则 `router.run(user_input)` 判断 route：
   - `route="plan"` 且有 plan → 事件 `route.decided`/`plan.created` → **将 Plan 写入 `context_manager.set_plan()`（Router 生成后直接入 ctx，下游不再传 plan 对象）** → 构造 `react_loop_fn` 闭包 → `plan_executor.run(...)`
   - `route="direct"` → 直接 `react_loop.run(...)`

**事件发布**：`run.status_changed`（routing→running）、`route.decided`、`plan.created`（含 plan_id、goal、project_path、steps、revision=1）。

### 1.2 graph/executor.py —— 拓扑工具函数

**关键函数**：`get_next_layer(steps: list[PlanStep]) -> list[PlanStep]` —— 从剩余步骤中取 **in_degree=0 的一层**（仅看剩余步骤内部依赖，忽略已完成 id），按 id 排序。供 PlanExecutorNode 每轮调用。

### 1.3 graph/nodes/router.py —— 路由判断 + 计划生成

**关键类/数据**：
- `PlanStepSchema`/`RouteSchema`（Pydantic）：`route: Literal["direct","plan"]`、`project_path`、`goal`、`steps[{id, description, depends_on}]`、`thought`
- 兼容数据类：`PlanStep(id: str, description, depends_on)`、`Plan(goal, steps, project_path)`、`RouteResult(route, plan)`
- `RouterNode.run(user_input) -> RouteResult`

**核心实现**：
- 消息：`ctx.build_messages("router", route_instruction=...)`（ContextManager 注入历史+指令）
- **强制 LiteLLM**：`if not llm.is_provider_type(LiteLLMProvider): raise` → 用 `instructor.from_litellm(completion, mode=MD_JSON)` 结构化解析（`temperature=0.1, max_retries=3`），避免 JSON_MODE 兼容问题
- plan 分支：steps 为空降级 direct；**查询 Wiki 并注入**：`_query_wiki_context(goal, messages)` → `WikiMemory.select_relevant_pages(top_k=3)` → 拼接页面正文 → `ctx.set_wiki_context()`（标记 System Prompt 待重建）
- direct 分支：不查 Wiki，快速执行
- **任何异常降级为 direct**（容错设计）

### 1.4 graph/nodes/react_loop.py —— ReAct 循环（核心）

**双入口设计**：
1. **自由函数 `run_react_loop(...)`**（模块级，可复用）：通用循环核心，`MemoryExtractionNode` 等也可复用。参数：`messages, tools, max_iterations=MAX_ITERATIONS(100), on_tool_call, on_finish, on_assistant_message, console, silent, session, llm_service, execution_context`。
   - 每轮：`llm.chat(messages, tools, on_delta=流式回调)` → 更新 session token + 发布 `usage.updated`
   - `finish_reason=="stop"` → 发布 `assistant.completed`、`assistant_message_id`/`assistant_completed` 写回 ctx，`assistant_response_to_message` 追加回 messages → 返回 content
   - `finish_reason=="length"` → "错误：回复被截断"
   - tool_calls 循环：默认 `ToolRunner().run()` 执行，`RunCancelled` 透传，其他异常转字符串喂回 LLM；`tool_result_to_message(tc.id, result)` 追加
   - 迭代耗尽 → "错误：达到最大迭代次数"
2. **`ReactLoopNode.run(...)`**：图节点封装。
   - **记忆提取集成**：仅主 agent（`not is_sub_agent`）懒加载 `MemoryExtractionIntegration`，经 `agent.register_extractor()` 注册；`_on_assistant_message` 里 `session.add()` + `memory_integration.on_react_loop_end(session, 0)`
   - 工具集：主 agent `get_schemas()`（全量 15 个）/ 子 agent `get_schemas_for_sub_agent()`（8 个白名单）
   - `ctx.compress_if_needed()` 调用前压缩 → `ctx.build_messages("react")`
   - `RuntimeError` 捕获 → 权限不足面板（连续权限拒绝终止）

### 1.5 graph/nodes/plan_executor.py —— Plan-and-Execute + Replanning

**关键类**：`PlanExecutorNode`（内含 `ReplannerNode`）。

**执行循环**（状态全在 `ctx.plan_state`）：
```
while ps.remaining:
    layer = get_next_layer(ps.remaining)          # in_degree=0 一层
    is_serial = len(layer)==1
    if 串行: _run_serial(step)                     # 子 session 继承主 session 全部消息
    else:   _run_parallel(layer)                   # 线程并行，子 session 独立
    ps.remaining 移除已完成层
    new_remaining = replanner.run()               # 每层后必评估（含最后一层）
    new_remaining is None → DONE
    new_remaining != 原 → 发 plan.updated（revision+1，合并 known_steps）
    ctx.update_remaining(new_remaining)
```

**串行 vs 并行子任务**（关键差异）：
- 串行 `_run_serial`：prompt = `【计划目标】+【项目根目录】+【前置步骤结果】(past_steps 的 to_context_prompt 拼接) +【当前任务】+【要求】`；经 `run_sub_agent_fn(task, workspace, f"step-{id}", console, child_context)` 执行，结果 `ctx.add_step_result(StepResult(...))`
- 并行 `_run_parallel`：`threading.Thread` 每步一线程（daemon，名 `plan-step-{id}`），`console=None` 静默执行，`results` 字典 + `notify_queue` 汇总；事件 `step.completed`/`step.failed` 在线程内发布

**结束整合**：全部步骤完成后，把 `ps.past_steps` 拼成 Markdown summary 作为 user 消息追加到**主 session** → `ctx.clear_plan()` → 调 `react_loop_fn()` 交主 agent 整合回复。

**ExecutionContext 子上下文**：`execution_context.child_agent(name=f"步骤 {step.id}", task=task, step_id=...)` 为每步生成独立 agent_id/agent_name（sandbox_profile=步骤名）。

### 1.6 graph/nodes/replanner.py —— 重规划节点

**关键类**：`ReplanSchema(thought, status: Literal["done","continue"], project_path, steps)`；`ReplannerNode.run() -> list[PlanStep] | None`（None=DONE）。

**核心实现**：
- 消息：`ctx.build_messages("replanner", replanner_instruction=...)`（战报=已完成步骤+真实结果注入）
- instructor MD_JSON 结构化（同 router：仅 LiteLLM，`make_instructor_kwargs(messages, 0.1)` + `response_model` + `max_retries=3`）
- **调用失败 → 保持原计划继续**（`return ctx.plan_state.remaining`）
- `status=="done"` → None；project_path 更新；steps 空视为 DONE
- 内置重规划铁律 prompt：绝对路径强制令、防幻觉、破除死循环（失败步骤禁止原样重复）、上下文自包含、动态并发视野（depends_on:[] 并行优先）；**steps id 必须从 1 重新连续编号**

### 1.7 graph/nodes/tool_runner.py —— 工具执行边界（唯一执行入口）

**关键类**：`ToolRunner`（dataclass：`console, auto_confirm, max_permission_denied=2, execution_context`）。

**`run(name, arguments)` 流程**：
1. `_parse_arguments` JSON 解析（非 dict 包 `{"value":...}`，失败包 `{"raw":...}`）
2. **授权**：`need_confirm(name) and not auto_confirm` → `_request_approval()`（execution_context 存在走 `approval_provider.request(...)`，否则终端 `input()`）；拒绝 → 发 `tool.denied` 返回 "用户拒绝执行此操作"
3. 发 `tool.started` → `_capture_files`（显式文件参数 path/file_path/target_path/destination 的 SHA256 指纹）
4. `execute(name, arguments)`（tools.registry）
5. 成功 → `tool.completed` + `_publish_file_changes`（前后指纹比对：created/deleted/modified → `file.changed`）
6. 异常处理：`RunCancelled` 透传（发 `tool.failed`）；`PermissionError` 计数，≥2 次 → `RuntimeError("连续 2 次权限拒绝，任务终止")`；其他异常 → 发 `tool.failed` 返回错误字符串（喂回 LLM 继续）

### 1.8 graph/nodes/memory_extraction.py —— 会话记忆提取节点

**分层**：Node（编排：状态/队列/线程）↔ `wiki/extraction/` 包（ExtractionRunner/Config/策略/LLM）。

**关键类**：
- `MemoryExtractionNode(memory: WikiMemory, config=ExtractionConfig, llm_analyzer=None)`
  - 状态：`_tokens_at_last_extraction`、`_last_message_uuid`、`_is_initialized`、`_cooldown_tracker`、`_pending_extractions` 队列
  - **`should_extract(messages, token_count)`**（对齐 Claude Code shouldExtractMemory）：初始化阈值（`minimum_message_tokens_to_init`）→ token 增长阈值（`minimum_tokens_between_update`）→ 工具调用阈值（`count_tool_calls_since` + 最后消息无工具调用的自然间隙）
  - `check_and_extract` → `_trigger_extraction`（runner 构建数据入队）
  - `flush_pending`：批量出队（`max_pending`）→ `runner.analyze_with_llm`（LLM 判 needs_update + pages）→ `runner.process_extraction_result`（create→save_page / append→append_page upsert）→ 成功后 `context_manager.invalidate_cache()`
  - token 估算：中文 2 字≈1 token、英文 4 字≈1 token、tool_use 按 `len(json)//2`、兜底 `len(text)//3`
- `MemoryExtractionIntegration`：`on_react_loop_end(session, round)` → 估算 token → check_and_extract → pending ≥ max_pending 时**后台线程** flush_pending（不阻塞 CLI）

### 1.9 graph/nodes/message_codec.py —— 消息序列化

- `assistant_response_to_message(response: LLMResponse) -> dict`：LLMResponse → 可回放 assistant 消息（含 reasoning_content、tool_calls 数组，thought_signature 存 `__thought_signature__`）
- `tool_result_to_message(tool_call_id, content) -> dict`：`{"role":"tool", "tool_call_id", "content"}`

### 1.10 graph/nodes/wiki_query.py —— Wiki 精排注入节点

**关键类**：`WikiQueryResult(pages, injected_context)`（is_empty/page_count 属性）；`WikiQueryNode(memory_dir=None, top_k=3)`。

**`run(user_input, plan_goal, messages)` 流程**：query = plan_goal or user_input → `wiki.select_relevant_pages(query, messages, top_k)`（两阶段：关键词初筛+LLM 精排）→ 构建 pages_detail（name/content/description/tags/related）→ `_build_injected_context`（"## Wiki 知识库" 格式）→ 返回结果供注入 system prompt。

> ⚠️ **缺陷发现**：第 112 行 `from qrclaw.graph.context import get_context_manager` —— `graph/` 下**不存在** `context.py`（实际是 `qrclaw.memory.context.context_manager`），当 `memory_dir` 为 None 且走默认分支时必抛 ImportError。当前 Router 场景未触发（走 `_query_wiki_context`），但独立使用 WikiQueryNode 无 memory_dir 会崩溃。

---

## 2. execution/ —— 运行协议层（事件驱动运行态）

### 2.1 execution/context.py —— 运行上下文原语

- `RunCancelled(RuntimeError)`：取消中断异常
- `CancellationToken`：threading.Event 封装（is_cancelled / cancel / raise_if_cancelled）
- `ApprovalProvider(Protocol)`：`request(context, *, approval_id, tool_call_id, tool_name, arguments) -> bool`
- `ExecutionContext`（dataclass slots）：`conversation_id, run_id, agent_id, agent_name, task, event_sink, cancellation, approval_provider, workspace_path, sandbox_profile, parent_agent_id, step_id, plan_id, assistant_message_id, assistant_completed`
  - `publish(event_type, data)` → 委托 event_sink
  - `check_cancelled()` → 抛 RunCancelled
  - `child_agent(name, task, step_id)` → `dataclasses.replace` 生成子上下文（新 agent_id、parent 链、sandbox_profile=name）
  - `new_id(prefix)` → `{prefix}_{uuid4().hex}`

### 2.2 execution/models.py —— 领域模型（Pydantic）

**枚举**：`RunStatus`（queued/routing/running/waiting_approval/cancelling/completed/failed/cancelled）、`AgentStatus`、`StepStatus`、`ToolCallStatus`（含 denied）。

**模型**：
- `EventEnvelope`（WS 与持久化共用）：version=1、event_id、seq、timestamp、conversation_id、run_id、agent_id、parent_agent_id、type、data
- `RunModel`：run_id、conversation_id、status、route、goal、last_seq、error
- `PlanModel`/`PlanStepModel`：plan_id、goal、project_path、steps（step_id/description/depends_on/status/output）、revision
- `AgentModel`：agent_id、parent_agent_id、run_id、step_id、name、task、status、current_action、decision_summary、started_at/completed_at、result、error
- `ToolCallModel`/`ApprovalModel`/`AssistantMessageModel`
- `RunSnapshot`（**完整快照**）：run + plan + agents + tool_calls + pending_approvals + messages + file_changes + usage —— 页面首次加载/断线恢复用

### 2.3 execution/projector.py —— 事件投影（Event Sourcing 读取侧）

**关键类**：`RunProjector(snapshot)`。

- `apply(event)`：**幂等**——`event.seq <= snapshot.run.last_seq` 直接跳过；更新 last_seq/updated_at；`getattr(self, f"_on_{type.replace('.','_')}")` 分发
- 全部 `_on_*` handler（约 24 个）：
  - run：`run_started`/`run_status_changed`/`route_decided`/`run_completed`/`run_failed`/`run_cancelled`
  - plan：`plan_created`/`plan_updated`（合并既有步骤的 status/output，防覆盖）
  - step：`step_started`/`step_completed`/`step_failed`
  - agent：`agent_started`（无则新建 AgentModel）/`agent_progress`/`agent_completed`/`agent_failed`/`agent_cancelled`
  - tool：`tool_approval_required`（建 ToolCallModel WAITING_APPROVAL + ApprovalModel + agent 置 WAITING_APPROVAL）/`tool_approval_resolved`/`tool_started`/`tool_completed`/`tool_failed`/`tool_denied`
  - 其他：`assistant_delta`（**流式增量拼接** content）/`assistant_completed`/`usage_updated`/`file_changed`
- `_finish_run`：failed/cancelled 时**级联收敛**所有 PENDING/RUNNING/WAITING 的 agent/step/tool 状态，清空 pending_approvals
- `create_snapshot(run_id, conversation_id, goal)` 工厂

### 2.4 execution/service.py —— 运行服务（编排核心）

**关键类**：
- `_RunHandle`：projector + CancellationToken + RLock + Condition(changed) + thread
- `_PendingApproval`：context + Event + decision
- `RuntimeApprovalProvider`：`request()` 把授权挂到 `_pending_approvals` → 发 `tool.approval_required`（含 sandbox 开关状态）→ 轮询 `pending.event.wait(0.2)` 循环 + `check_cancelled()` → 返回 `decision=="allow_once"`
- `RunService(workspace, database_path, agent_runner, access_token)`
  - 初始化：access_token 写入 `workspace.root/local_access_token`（chmod 0600）；`RuntimeStore(workspace.root/runtime.sqlite3)`；`_restore_snapshots()` 重启恢复——异常退出（QUEUED/ROUTING/RUNNING/WAITING_APPROVAL/CANCELLING）统一发 `run.failed(code="service_restarted")`
  - **会话 API**：create_conversation（校验 workspace 存在）/list/get/update/delete_conversation（有活跃运行禁止删）/get_messages（读 Session JSON 文件，uuid→message_id）
  - **`start_run(conversation_id, content, client_request_id)`**：client_request_id 幂等（重复请求返回既有快照）；**同时只允许一个活跃运行**（有则抛错）；后台 daemon 线程 `_execute_run`
  - **`_execute_run`**：构造 root ExecutionContext → 发 `run.started`/`agent.started` → `import qrclaw.tools`（触发注册）→ `agent.run(...)` 或注入的 `agent_runner` → 完成后若 `not assistant_completed` 补发 delta/completed → `agent.completed`/`run.completed`；RunCancelled → `run.cancelled`；异常 → `run.failed`
  - **`_publish`**：构造 EventEnvelope（seq 自增）→ `store.append_event` → `projector.apply` → `store.save_snapshot` → `changed.notify_all()`（**事件先持久化再投影**）
  - 查询：get_snapshot（deep copy）/get_events(after_seq)/wait_for_events（Condition 等待，WebSocket 桥接用）
  - **`cancel_run`**：发 `run.status_changed=cancelling` → cancellation.cancel() → 释放挂起授权
  - **`resolve_approval(approval_id, decision∈{allow_once,deny})`**：幂等（_resolved_approvals 去重）→ 发 `tool.approval_resolved` → 唤醒 pending.event
  - `_refresh_waiting_status`：**仅当所有可执行叶子 Agent 均 waiting_approval 时** run 状态才切 waiting_approval（避免并行中一个子任务等授权就误判整体阻塞）

### 2.5 execution/store.py —— SQLite 持久化

- `RuntimeStore(path)`：WAL 模式 + foreign_keys=ON + RLock；表：`conversations`、`runs`（snapshot_json + client_request_id UNIQUE 幂等键）、`events`（(run_id,seq) 主键，event_id UNIQUE）
- 关键方法：create_conversation/list/get/update/delete_conversation、create_run（client_request_id 幂等）、save_snapshot（原子更新 run+conversation updated_at）、get_snapshot/list_snapshots、append_event、events_after(after_seq, limit=1000)、find_run_by_client_request

---

## 3. providers/ —— LLM 抽象层

### 3.1 providers/base.py —— 抽象基类与数据契约

- `ToolCall(id, name, arguments: str, thought_signature=None)`：arguments 是 **JSON 字符串**；thought_signature 为 Vertex AI thinking 模型 base64 专用
- `LLMResponse(content, reasoning_content, tool_calls, finish_reason("stop"|"tool_calls"|"length"), prompt_tokens, completion_tokens, total_tokens, raw)`
- `LLMProvider(ABC)`：`chat(messages, tools, json_mode, temperature, on_delta)` —— json_mode 用 OpenAI response_format，不支持的 provider 忽略（调用方正则兜底）；on_delta 非 None 时启用流式

### 3.2 providers/litellm_provider.py —— LiteLLM 桥接

**初始化**：配置优先级——显式参数 > 环境常量（`LITELLM_API_KEY/MODEL/BASE_URL/API_BASE/PROXY_URL`）；`_infer_provider(model, base_url)` 根据 base_url host 自动补 provider 前缀（`PROVIDER_PREFIX_MAP`：openai/minimax/deepseek/anthropic/vertex_ai/azure/cohere/mistral/huggingface/openrouter），模型名含 `/` 视为已有前缀；全局设置 `litellm.drop_params=True`、`ssl_verify=False`。

**`chat()`（非流式）**：
- `_sanitize` 过滤顶层 null 字段（refusal/annotations/audio/function_call/reasoning_content）+ content None→""
- **指数退避重试 3 次**：仅对 RateLimitError/ServiceUnavailableError/APIConnectionError/APIError（APIError 按 status_code），未知异常直接抛 RuntimeError
- 解析：`_extract_response_reasoning`（message.reasoning_content 或 provider_specific_fields）；tool_calls 提取；**finish_reason 修正**——有 tool_calls 且非 tool_calls/stop 时强制置 tool_calls

**`_chat_streaming()`（流式）**：`stream=True + stream_options={"include_usage": True}`；逐 chunk 合并 content（同步 on_delta 回调）、reasoning_content、按 index 拼接 tool_calls 分片；usage 从 chunk 收集；重试 3 次但 **emitted 后不再重试**（避免重复输出）。

**`make_instructor_kwargs(messages, temperature=0.1)`**：给 instructor 的 kwargs（model/messages/temperature/api_key/api_base），调用方追加 response_model/max_retries——router/replanner 走此路径。

### 3.3 providers/__init__.py —— 注册表与全局 Provider

- `_REGISTRY = {"litellm": "qrclaw.providers.litellm_provider.LiteLLMProvider"}`（当前唯一渠道）
- `_load_provider()`：importlib 动态加载（按 `config.LLM_PROVIDER`）
- `reload_provider()`：读 `config_manager.get_config()["llm"]` 重建全局 provider（热更新，仅支持 litellm）
- **模块级单例**：`provider: LLMProvider = _load_provider()` —— import 期即加载

---

## 4. 模块间依赖关系矩阵

| 模块 | 依赖（import 方向） |
|---|---|
| graph/runner | memory.context.session、workspace、graph.nodes.router/react_loop/plan_executor、logger、memory.context.context_manager（延迟） |
| graph/nodes/router | providers.litellm_provider（类型探测）、llm_service、memory.context.context_manager、memory.wiki.wiki_memory、agent（延迟，取 workspace）、instructor+litellm（延迟） |
| graph/nodes/react_loop | config(MAX_ITERATIONS)、tools.registry、memory.context.session/context_manager、llm_service、workspace、message_codec、tool_runner、execution.context(RunCancelled)、memory_extraction（延迟）、agent.register_extractor（延迟） |
| graph/nodes/plan_executor | memory.context.session/step_result/context_manager、workspace、graph.executor、replanner、logger |
| graph/nodes/replanner | providers.litellm_provider、llm_service、router(PlanStep)、memory.context.context_manager、instructor+litellm（延迟） |
| graph/nodes/tool_runner | execution.context、tools.registry(execute/need_confirm) |
| graph/nodes/memory_extraction | memory.wiki、memory.wiki.extraction.*、memory.context.context_manager（延迟） |
| graph/nodes/wiki_query | memory.wiki.wiki_memory、（缺陷：qrclaw.graph.context 不存在） |
| execution/service | execution.context/models/projector/store、memory.context.session、workspace、logger、（延迟）qrclaw.tools、agent.run |
| execution/* | 仅依赖 execution 内部 + pydantic + sqlite3 + rich；**不依赖 graph/providers** |
| providers | config、logger；litellm_provider 额外依赖 litellm 库 |

**跨模块关键闭环**：
1. `execution.RunService._execute_run` → `agent.run(execution_context=root_context)` → `GraphRunner.run` → nodes 全部通过 `execution_context.publish/check_cancelled/child_agent` 回写事件 → `RunProjector` 归并 → `RunSnapshot` 供前端轮询/WS
2. `router/replanner` 强制 LiteLLM：`llm_service.is_provider_type(LiteLLMProvider)` 探测 → `LiteLLMProvider.make_instructor_kwargs` → instructor MD_JSON 结构化
3. `react_loop` → `ToolRunner.run` → `tools.registry.execute`（15 工具）→ 工具内部再回依赖 sandbox/memory/wiki/agent
4. 计划态 `PlanState`（memory.context.context_manager）是 router/plan_executor/replanner 三节点的**共享状态总线**

---

## 5. 数据流总结

**CLI 路径**：`cli/app.py → agent.run() → GraphRunner.run() → {Router → ReactLoop | Router → PlanExecutor → (并行/串行子 agent → Replanner 循环) → ReactLoop 整合}`

**Web 路径**：`server → RunService.start_run → _execute_run（daemon 线程）→ agent.run(execution_context) → 事件流(_publish: store.append_event → projector.apply → save_snapshot → notify) → 前端 get_snapshot / wait_for_events / resolve_approval / cancel_run`

**事件类型全集**（约 27 种）：`run.started/status_changed/completed/failed/cancelled`、`route.decided`、`plan.created/updated`、`step.started/completed/failed`、`agent.started/progress/completed/failed/cancelled`、`tool.approval_required/approval_resolved/started/completed/failed/denied`、`assistant.delta/completed`、`usage.updated`、`file.changed`、`run.status_changed`

---

## 6. 异常与隐患清单

1. **wiki_query.py:112** `from qrclaw.graph.context import get_context_manager` —— 模块不存在（应为 `qrclaw.memory.context.context_manager`），memory_dir=None 分支必炸（见 1.10）
2. **router/replanner 硬依赖 LiteLLMProvider**：非 LiteLLM 渠道下 router 直接抛 RuntimeError 降级 direct、replanner 保持原计划——未来多 provider 需泛化
3. **`ExecutionContext.sandbox_profile` 在 `child_agent` 中被设为子 agent 名字**（如 "步骤 1"），与 sandbox 配置的 agent 名约定耦合，需核对 `permissions.yaml` 命名
4. **`RunService` 同时只允许一个活跃运行**：前端并发多任务会抛 RuntimeError（设计取舍，需前端串行）
5. `GraphRunner` 是 if/else 条件边而非真正图数据结构；`strategies/` 目录为空占位
6. `_run_parallel` 中 `results[step.id]` 失败时写入错误字符串，成功/失败以 `notify_queue` 为准，异常步结果**照常**进 StepResult 让 Replanner 决策（符合重规划铁律 3）
7. `provider` 模块级单例 import 期加载，`reload_provider()` 提供热更新但仅 litellm
