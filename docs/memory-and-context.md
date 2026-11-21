# QRClaw 记忆系统与上下文管理

QRClaw 的"记忆"分两层：

| 层 | 存什么 | 存在哪 | 生命周期 |
|---|---|---|---|
| **短期：会话上下文** | 本次对话的消息、事件、token 用量 | `~/.qrclaw/agents/<agent>/sessions/<id>.json` | 单个会话 |
| **长期：Wiki 记忆** | 用户偏好、项目事实、决策等可复用知识 | `~/.qrclaw/agents/<agent>/memory/pages/*.md` | 跨会话 |

代码都在 `qrclaw/memory/` 下：`context/` 管短期，`wiki/` 管长期，`compression/` 负责压缩。

---

## 一、上下文管理（短期）

### 1. 核心对象

- **`Session`**（`memory/context/session/session.py`）：一个会话 = 一个 JSON 文件，里面有 `messages`、`events`、`usage`、`title`、`history_version`。每次 `add()` 都加文件锁、重新读盘、追加、原子写回，所以多进程（CLI / Web 服务）共用同一会话也不会写乱。
- **`ContextManager`**（`memory/context/runtime/manager.py`）：轻门面，按线程绑定（`init_context_manager` / `get_context_manager`），把下面三样串起来：
  - `SessionStore` — 包一层 Session
  - `PlanController` — 当前计划的目标、已完成步骤、剩余步骤
  - `SystemPromptCache` — system prompt 缓存，内容变化时 `invalidate()`

### 2. 给模型拼消息

`ContextManager.build_messages(role)` 按调用方角色给不同视图（`memory/context/prompt/builder.py`）：

| role | 内容 |
|---|---|
| `react` | system prompt + 全部会话消息 |
| `router` | 路由提示 + 只保留用户消息、无工具调用的助手消息、执行报告（去掉工具噪音） |
| `replanner` | 计划目标 + 已完成步骤结果 + 剩余步骤（不带聊天历史） |

system prompt 由 `prompt_builder.build_system_prompt()` 生成，包含 Agent 设定、技能、心跳任务，以及 **Wiki 索引**（见下文）。生成后会算出"提示词 + 工具定义"的 token 开销存进会话，用于估算总用量：

```
context_tokens = 消息 token + system prompt/工具 token
```

token 计数用 LiteLLM 的 `token_counter`（`memory/token_utils.py`），按当前模型选分词器。

### 3. 自动压缩

每次 ReactLoop 调模型前都会调用 `ctx.compress_if_needed()`：

1. `context_tokens` 超过阈值（默认模型窗口的 **90%**）才压缩。
2. `_pick_recent()` 从最新消息往回保留，预算为 `compress.recent_max_tokens`（默认 1536 token）；不会把 `tool_calls` 和对应的 `tool` 结果拆开。
3. 更早的消息交给 LLM 生成结构化摘要（用户信息 / 已完成任务 / 关键结论 / 待处理事项），目标约窗口的 10%。
4. 历史被替换为：`[SUMMARY] 摘要` + 保留的近期消息。如果旧消息里已有 `[SUMMARY]`，会一起再压一次（递归摘要）。
5. 压缩后若总量超过 25%，再收紧近期消息预算。

几个保护措施：
- 压缩前先调用记忆提取的 `preserve_before_compression()`，把即将被丢掉的原文存进待提取队列，**长期记忆不会因压缩丢信息**。
- 写回前检查会话是否被其他进程改过，改过就放弃。
- 压缩开始/完成/失败都会写一条 `compression` 事件，前端可展示进度。

相关配置（`config.yaml` 的 `llm` / `compress` 段，可用环境变量覆盖）：

| 变量 | 默认 | 含义 |
|---|---|---|
| `MODEL_MAX_TOKENS` | 485000 | 模型上下文窗口 |
| `COMPRESS_THRESHOLD_RATIO` | 0.9 | 触发压缩的比例 |
| `COMPRESS_RECENT_MAX_TOKENS` | 1536 | 保留近期消息的预算 |
| `COMPRESS_SUMMARY_MAX_TOKENS` | 2560 | 摘要上限（目前未被压缩逻辑使用；摘要目标固定为窗口 × 10%） |

### 4. 计划模式下的上下文

Router 判定为 `plan` 后：
- `ctx.set_plan()` 记录目标和步骤；
- 每个步骤交给**子 Agent** 执行，子 Agent 有自己独立的会话（`sub-*.json`），不共享主会话历史。传给它的只有：计划目标、项目根目录、**前置步骤结果**（`StepResult.to_context_prompt()`）和当前任务；
- 步骤完成后结果通过 `ctx.add_step_result()` 存入计划状态，供后续步骤和 Replanner 使用；
- 计划结束 `ctx.clear_plan()`，同时让 system prompt 失效重建。

---

## 二、记忆系统（长期 Wiki）

### 1. 存储结构

```
memory/
├── pages/<页面名>.md        # 每页一个 Markdown，带 frontmatter（description/tags/related/revision/status…）
├── index.json               # 页面元数据索引；服务启动时从 pages/ 重建
├── index.md                 # 由 index.json 生成的可读索引（只供查看，不注入提示词）
├── log.md                   # 操作日志
├── SCHEMA.md                # 页面规范
├── .wiki-state.json         # 来源追溯：页面版本、已删除页面、来源消息
└── .extractions/<会话>.json # 每个会话的待提取进度
```

入口是 `WikiMemory`（`memory/wiki/wiki_memory.py`），同一目录单例。主要方法：`save_page` / `get_page` / `delete_page` / `list_pages` / `search_pages` / `select_relevant_pages` / `load_index`。

### 2. 记忆怎么写进去

**自动提取**（`graph/nodes/memory_extraction.py`）：

1. 每次请求开始 `on_run_start()`：暂停计时，记下活动时间。
2. 请求结束 `on_react_loop_end()`：收集新的用户/助手正文（过滤掉工具消息、执行报告、`[SUMMARY]`），放进该会话的待处理批次，持久化到 `.extractions/`。
3. 满足以下条件之一才安排提取：
   - 累计 ≥ 3 轮完整对话，且**闲置 120 秒**；
   - 或待处理正文 ≥ 5000 token（长对话，不等闲置）；
   - 或压缩前保存的原文（强制处理）。
   两次提取之间至少冷却 300 秒。
4. 后台线程调用 LLM：输入"现有 Wiki 索引 + 对话正文"，输出 `create / append / skip` 操作列表。`append` 会再走一次合并（Consolidation），把新旧内容整理成一份完整正文后替换旧页。
5. 只有用户来源支持的事实才能写入；助手的推测、一次性回复不算。

**主动写入**：仅在用户明确要求“记住”“以后都这样”时，Agent 才调用 `write_wiki_page`，通过 `write_now()` 立刻执行，不受轮次/闲置/冷却限制。已分析的会话正文从自动批次中移除，避免重复整理；分析期间新增或编辑的正文保留，其他长期信息由后台自动整理。

**一致性保护**：
- 用户编辑或删除了某轮消息 → 对应待提取内容撤销；来源失效的页面进入修复队列，由 `WikiRepairWorker` 在闲置时按剩余有效来源重建。
- 写页面时校验版本号，整理期间页面被改过就放弃旧结果。
- 用户主动删除的页面，旧的后台任务不能再把它写回来。
- 提取失败时原文留在磁盘，冷却后重试；服务重启后 `resume()` 续接。

可调参数：`config.yaml` 的 `memory` 段（`init_threshold`、`update_interval`、`min_turns`、`idle_seconds`、`cooldown_seconds`），或对应的环境变量 `MEMORY_INIT_THRESHOLD`、`MEMORY_UPDATE_INTERVAL`、`MEMORY_MIN_TURNS`、`MEMORY_IDLE_SECONDS`、`MEMORY_COOLDOWN_SECONDS`。修改后重启生效。

### 3. 记忆怎么读出来

有三种途径，从轻到重：

1. **索引常驻**：每次构建 system prompt 都注入 Wiki 索引（`WikiMemory.load_index()` 扫描 `pages/`，每个可检索页面一行"名称：描述"），模型据此知道有哪些知识。
2. **工具按需读**：模型用 `search_wiki` / `list_wiki_pages` 定位，再用 `read_wiki_page` 分页读正文。`search_wiki` 合并关键词命中和上面同一套 LLM 目录选页，每次调用会多一次模型请求。同一版本的同一段只给一次，避免重复占上下文。
3. **计划模式自动召回**：Router 判定为 `plan` 时，`WikiQueryNode` 以计划目标为查询调用 `select_relevant_pages()`：计划目标里直接出现的页面名优先；否则把全部可检索页面的「名称：描述」目录交给 LLM，取相关度 ≥ 0.6 的前 3 页；LLM 没选出页面时退回关键词搜索。选中页面的正文直接注入 system prompt。`direct` 模式不做这一步，保证简单任务够快。

来源失效或正在整理的页面不会暴露给 Agent（`WikiMemory.retrievable()`）。

---

## 三、一次请求的完整流程

```
用户输入
  │
  ├─ memory_integration.on_run_start()      暂停记忆提取计时
  ├─ init_context_manager(session, ...)     绑定上下文
  │
  ├─ Router ──direct──→ ReactLoop
  │     └──plan──→ WikiQueryNode 召回相关页面 → 注入 system prompt
  │                PlanExecutor → 子 Agent 逐步执行（只拿前置步骤结果）
  │                            → ReactLoop 汇总
  │
  │   ReactLoop 每次调模型前：
  │     compress_if_needed()  超 90% 就摘要压缩（压缩前原文先交给记忆提取）
  │     build_messages("react")
  │
  └─ memory_integration.on_react_loop_end() 收集新正文 → 闲置/长对话时后台提取到 Wiki
```

---

## 四、关键文件速查

| 文件 | 作用 |
|---|---|
| `memory/context/runtime/manager.py` | ContextManager：串联会话、计划、prompt 缓存，触发压缩 |
| `memory/context/session/session.py` | 会话持久化、token 统计、编辑/删除最后一轮 |
| `memory/context/prompt/builder.py` | 按 react / router / replanner 拼消息 |
| `memory/context/plan/` | 计划状态与步骤结果 |
| `memory/compression/compressor.py` | 递归摘要压缩 |
| `memory/token_utils.py` | token 计数 |
| `prompt_builder.py` | system prompt 生成（含 Wiki 索引注入） |
| `memory/wiki/wiki_memory.py` | Wiki 页面增删改查 |
| `memory/wiki/extraction/` | 提取提示词、LLM 调用、合并整理 |
| `memory/wiki/provenance.py` | 来源追溯与撤销 |
| `memory/wiki/repair.py` | 来源失效后的后台修复 |
| `graph/nodes/memory_extraction.py` | 自动提取的批处理、闲置计时、后台线程 |
| `graph/nodes/wiki_query.py` | 计划模式下的 Wiki 召回 |
| `tools/wiki_tools.py` | 暴露给 Agent 的 Wiki 工具 |
