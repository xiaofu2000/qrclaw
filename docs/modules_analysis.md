# QRClaw 核心模块源码分析

> 分析范围：`qrclaw/memory`、`qrclaw/sandbox`、`qrclaw/tools` 三个子模块
> 分析方式：直接阅读源码（非静态工具生成）
> 生成时间：2026-04-10

---

## 1. memory 模块 —— 记忆系统

**目录**：`qrclaw/memory/`（36 个 .py 文件）
**定位**：Agent 的多层记忆系统。核心是 LLM Wiki 架构的长期记忆 + 会话短期记忆 + 上下文组装与压缩。

### 1.1 职责

| 子目录 | 职责 |
|---|---|
| `wiki/` | **长期记忆（核心）**。LLM Wiki 架构：页面平铺 `pages/*.md`，`index.json`（结构化索引）与 `index.md`（人类可读目录，注入 system prompt）双向同步，`log.md` 追加操作日志 |
| `wiki/extraction/` | 记忆提取子系统：对话→LLM 分析→拆分/追加/合并 Wiki 页面的自动化管线（阈值触发 + instructor 结构化输出） |
| `wiki/selection/` | LLM 智能页面选择器：根据当前对话上下文（保留最近 3 轮、过滤工具调用）从 index 中选相关页面 |
| `storage/` | `MemoryIndexer`：兼容旧 Claude Code 四分类（user/feedback/project/reference）的 MEMORY.md 索引管理，内部已转调 wiki 的 IndexManager |
| `context/` | `Session`（短期会话：JSON 落盘、uuid 注入、token 统计）、`StepResult`（计划步骤结果）、`ContextManager`（线程级单例：统一组装各角色 messages、管理 PlanState、压缩判断、System Prompt Dirty-Flag 缓存） |
| `compression/` | 上下文压缩：递归摘要（检测 `[SUMMARY]` 旧摘要合并压缩）+ 目标占比 20%~25% 控制 + 滚动截断降级 |
| `types/` | `MemoryType`（四分类枚举）、`MemoryFile`、`MemoryIndex`、frontmatter 解析/构建工具（旧体系兼容层） |

**核心设计决策**：旧体系（MemoryType 四分类 + MEMORY.md）已被 **LLM Wiki 架构**替代。根目录的 `long_term.py` / `session.py` / `step_result.py` 均为兼容层，实际实现分别在 `wiki/`、`context/` 下。

### 1.2 对外暴露的主要接口

- **`WikiMemoryManager` / `WikiMemory`**（`wiki/manager.py`、`wiki/wiki_memory.py`）：按 `memory_dir` 缓存单例（`for_workspace` 类方法）。核心 API：`save_page`（upsert）、`append_page`、`get_page`、`list_pages`、`search_pages`（关键词全文粗排：name > description/tags > content）、`select_relevant_pages`（关键词初筛 + LLM 精排两阶段）、`fuzzy_find_name`（忽略大小写/空格/下划线的模糊名匹配）、`delete_page`、`load_index`（返回 index.md 供注入 system prompt）、`rebuild_index`、`clear_all`、`stats`
- **`IndexManager`**（`wiki/index.py`）：`upsert/remove/get_entry/all_entries/exists` + `rebuild_from_pages_dir`（从 pages/ 全量恢复）+ `load_index_md` + `append_log`
- **`WikiPage`**（`wiki/page.py`）：dataclass，序列化/反序列化带 frontmatter 的 Markdown；`to_index_entry()` 生成 index.json 条目；文件名直接使用页面名（保留中文可读性）
- **`ExtractionRunner`**（`wiki/extraction/runner.py`）：`trigger_extraction`（截取新增消息 + 构建索引摘要）、`build_prompt`、`analyze_with_llm`、`process_extraction_result`（create/append 动作写入，append 后批量 consolidate）、`consolidate_pages`（ThreadPoolExecutor 并发整理）
- **`WikiLLMAnalyzer`**（`wiki/extraction/llm.py`）：instructor 封装（`Mode.MD_JSON`），`analyze()` → `ExtractionSchema`（needs_update + pages[]），`consolidate()` → `ConsolidatePageSchema`，带 max_retries 重试
- **`should_extract`**（`wiki/extraction/strategies.py`）：触发条件 = token 增长 ≥ `minimum_tokens_between_update`（默认 4000）AND 工具调用数 ≥ `tool_calls_between_updates`（默认 3）
- **`WikiPageSelector`**（`wiki/selection/wiki_selector.py`）：`select(current_message, messages, keep_rounds=3)` → `SelectionResult`；`select_with_pages` 返回 WikiPage 对象
- **`Session`**（`context/session.py`）：`add/clear/update_tokens`；`add` 自动注入 `uuid`（12 位 hex）作为记忆提取截断锚点；`list_sessions` / `get_last_session_id` / `delete_session`（过滤 `sub-` 前缀子 agent 会话）
- **`ContextManager`**（`context/context_manager.py`）：线程级单例（`threading.local`），`init_context_manager(session, workspace, is_sub_agent)` / `get_context_manager()`；`build_messages(role)` 按角色（react/router/replanner）组装 messages
- **`summarize` / `truncate` / `count_tokens`**（`compression/`）：压缩入口；token 估算基于 tiktoken（`token_utils.py`），中文按 1.5 字/token、英文 0.25 字/token 估算
- **`ExtractionConfig`**（`wiki/extraction/config.py`）：dataclass，支持环境变量覆盖（`MEMORY_INIT_THRESHOLD`/`MEMORY_UPDATE_INTERVAL`/`MEMORY_TOOL_CALL_INTERVAL`）；初始化阈值 8000 tokens、更新间隔 4000 tokens

### 1.3 内部数据流

```
对话消息(messages) ──> [ExtractionRunner.trigger_extraction]
        │  截取 uuid 之后的新消息 + build_index_summary()
        ▼
提取 prompt ──> [WikiLLMAnalyzer.analyze] (instructor + LLMService)
        │  ExtractionSchema{needs_update, pages[{action: create|append, name, content, ...}]}
        ▼
[process_extraction_result] ──> WikiMemory.save_page / append_page
        │                       （append 的页面进 consolidate_pages 并发整理）
        ▼
pages/*.md ──> IndexManager.upsert ──> index.json ──> 重建 index.md
                                            │
                                     load_index() ──> 注入 system prompt（Agent 据此决定新建/更新）
                                            │
                             select_relevant_pages 时：index.md + 最近3轮对话 ──> LLM 精排 ──> 相关页面全文注入上下文
```

压缩链路：`ContextManager` 判断超阈值 → `summarize()` 递归摘要（旧 `[SUMMARY]` 合并）→ 重建 `session.messages = [SUMMARY] + recent`。

### 1.4 与其他模块的依赖关系

- **依赖**：`qrclaw.logger`（日志）、`qrclaw.llm.chat` / `qrclaw.llm_service.get_llm_service`（WikiPageSelector 与 ExtractionRunner 的 LLM 调用）、`qrclaw.prompt.build_system_prompt`（ContextManager，导致 `__init__.py` 对 ContextManager 做延迟导入防循环）、`qrclaw.config`（压缩阈值常量）、`qrclaw.workspace.Workspace`（memory_dir 来源）、可选第三方 `instructor`（缺失时 WikiLLMAnalyzer 置 None，惰性降级）
- **被依赖**：`graph/nodes/memory_extraction.py`（提取节点编排，ExtractionRunner 承担纯逻辑）、`graph/nodes/wiki_query.py`（wiki 查询节点）、`graph/nodes/router.py`（注入 wiki_context）、`tools/wiki_tools.py`（write_wiki_page 直接操作 extractor 的 `_pending_extractions` 队列 + `flush_pending`）、`workspace.py`（save_page/load_index 兼容接口对接）、`heartbeat.py`（周期触发提取）

---

## 2. sandbox 模块 —— Docker 沙箱隔离

**目录**：`qrclaw/sandbox/`（5 个 .py + tests/）
**定位**：基于 Docker 的 Agent 命令执行隔离层。配置驱动（`~/.qrclaw/permissions.yaml`），未启用时透明降级为宿主机直连执行。

### 2.1 职责

| 文件 | 职责 |
|---|---|
| `config.py` | pydantic 配置模型：`MountConfig`（host/container/mode ro|rw）、`SandboxConfig`（enabled/image/network/memory/pids_limit/mounts）、`AgentConfig`、`PermissionConfig`；`ConfigManager` 单例，首次运行自动生成默认 permissions.yaml；`BLOCKED_HOST_PATHS` 黑名单（/etc /root /proc /sys /dev /boot /run /var/run） |
| `validator.py` | 路径安全验证：`validate_mount_path`（禁止 docker.sock、黑名单前缀匹配，抛 `PathValidationError`）；`get_container_path`（host 路径 → 容器内路径映射） |
| `manager.py` | `SandboxManager` 单例：沙箱生命周期高层 API；`SandboxHandle`（dataclass，支持 `with` 上下文自动销毁）；沙箱未启用时返回 noop 句柄（`_exec_direct` 直连） |
| `container.py` | `ContainerManager`：直接与 docker CLI 交互（create/start/exec/stop/remove/exists/is_running/cleanup_exited）；`ContainerConfig` 安全默认（cap_drop=ALL、no-new-privileges、read-only 根文件系统、tmpfs、网络 none）；`ExecResult`/`ContainerError` |

**安全模型**：`--read-only` 根文件系统 + `--cap-drop ALL` + `--security-opt no-new-privileges` + `--network none`（默认）+ 内存/pids 限制 + 挂载白名单（仅 workspace 读写，其余只读）+ 禁止挂载 docker.sock 与系统敏感路径。

### 2.2 对外暴露的主要接口

- `create_sandbox(agent_id, workspace=None, mounts=None, config=None) -> SandboxHandle`：已存在则先销毁重建；合并三处挂载（config.mounts + permissions.yaml mounts + 调用方 mounts）后逐条验证
- `exec_in_sandbox(agent_id, command, cwd="/workspace", timeout=300, env=None) -> ExecResult`：无沙箱句柄时走 `_exec_direct`（subprocess shell 直连）
- `destroy_sandbox(agent_id, force=False) -> bool`：stop + remove
- `sandbox_manager`（全局单例）、`is_sandbox_enabled(agent_id)`（default agent 默认 false，其余默认 true）、`get_sandbox_config` / `get_sandbox_mounts`
- `validate_mount_path(host_path) -> bool`、`get_container_path(host_path, mounts) -> str|None`
- `ExecResult`：`exit_code/stdout/stderr/duration`；`ContainerError` 异常

### 2.3 内部数据流

```
tools/shell.run_shell ──> is_sandbox_enabled(agent_id)?
        ├─ False ──> _exec_direct: subprocess.Popen(shell=True, start_new_session)
        │              └ 支持 RunCancelled 取消（killpg SIGTERM→SIGKILL）、30min 超时
        └─ True ──> sandbox_manager.has_sandbox?
              ├─ 无 ──> create_sandbox: 合并 mounts → validate_mount_path → ContainerManager.create
              │           （docker create --read-only --cap-drop ALL ... sleep infinity）→ start
              └─ 有 ──> exec: is_running? 否则 start → docker exec -i -w cwd sh -c command
配置流：permissions.yaml ──> ConfigManager(单例) ──> AgentConfig ──> SandboxConfig ──> create 参数
```

### 2.4 与其他模块的依赖关系

- **依赖**：`qrclaw.logger`、`qrclaw.workspace.Workspace`（默认 workspace 目录，容器挂载为 `/workspace:rw`）
- **被依赖**：`tools/shell.py`（run_shell 的沙箱分支，从 `qrclaw.agent.get_execution_context` 取 agent_id/sandbox_profile）、`tools/spawn_agent.py`（子 agent 按配置自动建/毁沙箱）、`tools/filesystem.py`（`_resolve_path` 沙箱模式将 `/workspace/...` 映射到宿主机 workspace）、`cli/` 与 `server/`（权限配置界面）、`tools/agent_tools.py`（create_agent/delete_agent 读写 permissions.yaml）

---

## 3. tools 模块 —— 工具注册与执行

**目录**：`qrclaw/tools/`（12 个 .py）
**定位**：Agent 的能力层。装饰器注册制（`@register`）+ Pydantic 参数校验 + Agent 类型可见性控制 + 执行确认（confirm）。

### 3.1 职责

| 文件 | 注册的工具 | 职责要点 |
|---|---|---|
| `registry.py` | —（框架） | 注册表、schema 生成、Agent 白名单、统一执行入口 |
| `filesystem.py` | `read_file`、`write_file`、`list_directory`、`patch`、`grep_code` | 文件读写/目录/精确编辑/搜索；含设备路径、敏感路径（/etc、/boot、docker.sock）、二进制扩展名三重防护；读取循环检测（连续≥3 次警告、≥4 次阻断）、mtime 去重、陈旧检测（外部修改警告）；`patch` 双模式：replace（模糊匹配替换）+ V4A 多文件补丁 |
| `shell.py` | `run_shell`（confirm=True） | 自动路由沙箱/直连；独立进程组 + 取消令牌（RunCancelled）响应 |
| `web.py` | `web_search`、`web_extract`、`web_crawl` | SSRF 防护（RFC1918/loopback/link-local/CGNAT/元数据 hostname，198.18.x 仅警告）；多后端 fallback（Firecrawl > Tavily > Exa > Jina）；LLM 内容压缩（<5k 直接返回，5k-500k 单次摘要，>500k 分块 100k/块并行摘要后合成，2MB 硬上限）；base64 图片清理 |
| `wiki_tools.py` | `write_wiki_page`、`read_wiki_page`、`delete_wiki_page`（均限 MAIN agent） | 记忆读写入口；write 走提取管线（构造 extraction_data 入队 → 后台线程 flush_pending） |
| `skills.py` | `use_skill` | 从当前 agent workspace 的 skills/ 加载 SkillRegistry，返回技能完整执行指导 |
| `spawn_agent.py` | `spawn_agent` | 后台线程启动子 agent（共享父 workspace，禁止嵌套派生），可选建沙箱，完成自动打印 Panel |
| `wait_agents.py` | `wait_agents` | `thread.join()` 零 CPU 阻塞等待，汇总结果后清理任务池 |
| `agent_tools.py` | `create_agent`、`delete_agent`（confirm=True） | 创建/删除 agent 工作目录结构（sessions/logs/skills/memory/AGENT.md），同步写 permissions.yaml |
| `fuzzy_match.py` | —（辅助） | 模糊查找替换（容忍空白/缩进差异），供 patch replace 使用 |
| `patch_parser.py` | —（辅助） | V4A 补丁格式解析（Add/Update/Delete/Move + hunk）+ 应用，供 patch 工具使用 |

**共 15 个注册工具**。`run_shell`/`write_file`/`patch`/`delete_agent` 标记 `confirm=True`（执行前需用户批准）。

### 3.2 对外暴露的主要接口

- **`register(description, args_model, confirm=False, agents=None)`**：装饰器。从 Pydantic `model_json_schema()` 生成 OpenAI Tool Schema（递归展开 `$ref`、去 title，兼容 Gemini；无参工具给空 parameters 兼容 MiniMax）
- **`execute(name, arguments: str) -> str`**：统一执行入口。JSON 解析 → Pydantic 校验（`model(**raw_args)`）→ 调用 fn；异常统一返回错误字符串，不抛给上层
- **`get_schemas(agent_type)` / `get_schemas_for_sub_agent()`**：按 AgentType（MAIN=全量 / SUB=白名单 8 工具）返回可见 schema 列表。双层过滤：工具级 `agents` 字段 + Agent 白名单
- **`need_confirm(name)`**：查询工具是否需要确认
- **`AgentType`**：MAIN="main" / SUB="sub"；SUB 白名单 = read_file/write_file/str_replace/list_directory/grep_code/run_shell/web_search/web_fetch/use_skill
- 辅助导出：`set_console`、`get_task_pool`（spawn_agent 任务池，wait_agents 共享）

### 3.3 内部数据流

```
graph/nodes/tool_runner.py（执行边界）
   │ 1. 解析参数、check_cancelled、need_confirm？→ approval_provider 或终端 y/n
   │ 2. publish tool.started / 记录文件指纹(SHA256)
   ▼
tools.registry.execute(name, arguments_json)
   │ 3. json.loads → Pydantic 参数模型校验 → model_dump
   ▼
工具函数 fn（filesystem/shell/web/wiki/spawn...）
   │ 4. 返回 str 结果（错误也返回字符串）
   ▼
tool_runner: publish tool.completed / file.changed（指纹比对）→ 结果进对话消息

注册流：tools/__init__.py import 全部子模块 → @register 装饰器填充 _tools 字典
可见性流：LLM 请求 schema 时 get_schemas(agent_type) 按双层过滤返回
```

### 3.4 与其他模块的依赖关系

- **依赖**：`qrclaw.logger`；`qrclaw.tools.registry`（框架核心）；`qrclaw.agent`（get_workspace/get_execution_context/get_session/get_extractor/is_sub_agent/run_sub_agent，文件路径解析与子 agent 运行）；`qrclaw.sandbox`（shell 沙箱路由）；`qrclaw.web_search.runtime`（web_search 的后端运行时）；`qrclaw.llm_service`（web 内容压缩）；`qrclaw.memory.wiki`（wiki 工具）；`qrclaw.skills.registry`（use_skill）；`qrclaw.execution.context.RunCancelled`（取消协作）；`qrclaw.workspace`（AGENTS_ROOT）；`qrclaw.config`（file_read_max_chars）
- **被依赖**：`graph/nodes/tool_runner.py`（唯一执行边界，处理 confirm/事件发布/文件变更检测）、`graph/strategies/`（schema 注入 system prompt）、`cli/`（工具列表展示）、`server/`（approval provider 对接 web 端授权）、`memory/context/context_manager.py`（经 `tools/filesystem.get_read_files_summary` 感知已读文件，供压缩决策；`reset_file_dedup` 在压缩后重置去重）

---

## 4. 跨模块关系小结

```
                    ┌─────────────────────────────────────────────┐
                    │  graph/ 执行引擎 (tool_runner / nodes)      │
                    └───────┬───────────────┬─────────────────────┘
                            │               │
                tools.execute(name,args)    │ 会话/上下文
                            ▼               ▼
   ┌────────────────────────────┐   ┌─────────────────────────────┐
   │  tools/ 工具层 (15 工具)   │   │  memory/ 记忆层              │
   │  registry + 各工具实现     │   │  Session/ContextManager      │
   └───────┬────────────┬───────┘   │  WikiMemory + Extraction    │
           │            │           └──────────────┬──────────────┘
           │            │                          │
           │            └── wiki_tools ────────────┘（读/写记忆）
           │
           ▼
   ┌────────────────────────────┐
   │  sandbox/ 隔离层            │  ← shell/spawn_agent/filesystem 调用
   │  SandboxManager→Container  │
   └────────────────────────────┘

依赖方向：graph → tools → sandbox / memory / agent / llm_service / web_search
          memory → llm / prompt / config / workspace
          sandbox → workspace / agent（agent_id）
```

- **tools 是唯一工具执行入口**（LLM 只能通过 tool_runner → registry.execute 触发能力），sandbox 是其命令执行的后端隔离选项，memory 是其记忆读写后端。
- **memory 与 tools 通过 graph 节点解耦**：提取节点调用 ExtractionRunner（memory 内部），wiki 查询节点调用 WikiMemory，wiki 工具调用 extractor 队列。
- **sandbox 是可选开关**：`permissions.yaml` 按 agent 配置，关闭时全部透明降级为宿主机直连，工具层无感。
