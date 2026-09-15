# QRClaw 核心代码架构分析

> 分析对象：`qrclaw/` 包根目录下 10 个核心 Python 文件
> 版本：0.9.2
> 分析日期：由核心代码分析任务自动生成

---

## 1. 整体架构总览

QRClaw 是一个**运行在用户本地的自主 AI Agent**。入口层 → 执行引擎 → 记忆/工具/LLM 服务三层结构：

```
cli.py ──> cli/app.py（main）──> agent.run() ──> GraphRunner（graph/runner.py）
                                                      │
              ┌───────────────────────────────────────┘
              │ 委托执行（react_loop / plan_executor / replanner / router 等节点）
              ├── LLM 层：llm.py ──> llm_service.py ──> providers/（litellm_provider）
              ├── 记忆层：memory/（Wiki 记忆、ContextManager、压缩）
              ├── 工具层：tools/registry.py（get_schemas）
              ├── 技能层：skills/registry.py（SkillRegistry）
              ├── 配置层：config_manager.py ──> config.py（环境变量快照）
              ├── 工作空间：workspace.py（Workspace）
              ├── 心跳：heartbeat.py（Heartbeat + 子 agent 任务）
              └── MCP：mcp_integration/（可选，agent.py 内初始化）
```

**关键设计点**：
- **thread_local 状态隔离**：`agent.py` 用 `threading.local()` 管理 session/workspace/agent_depth/execution_context，支持多线程子 agent 并行。
- **配置两级传递**：`config_manager.py` 读 `~/.qrclaw/config.yaml` 注入环境变量 → `config.py` 在 import 时读环境变量生成模块级常量。
- **LLM 抽象两层**：`llm.py`（简单兼容层，返回 str）→ `llm_service.py`（LLMService，返回 LLMResponse，支持 tools/结构化输出）。
- **System Prompt 分段拼装**：`prompt.py` 仿 OpenClaw 分段设计，工具列表/技能/记忆索引/心跳任务动态注入。

---

## 2. 各文件逐一分析

### 2.1 `__init__.py` — 包入口

| 项 | 内容 |
|---|---|
| 职责 | 包版本声明 |
| 关键内容 | `__version__ = "0.9.2"` |
| 依赖 | 无 |

### 2.2 `agent.py` — Agent 入口 + 工具函数（核心中枢）

| 项 | 内容 |
|---|---|
| 职责 | thread_local 状态管理、`run()` 对外接口（委托 GraphRunner）、`run_sub_agent()` 子 agent 启动、MCP 生命周期管理、全局 Extractor 注册 |
| 关键函数 | 见下表 |

| 函数 | 作用 |
|---|---|
| `set_session / get_session` | thread_local 设置/获取 Session |
| `set_workspace / get_workspace` | thread_local 设置/获取 Workspace |
| `set_agent_depth / get_agent_depth / is_sub_agent` | 子 agent 深度控制（>0 即子 agent） |
| `get_agent_id` | 从 workspace 取 agent_id |
| `set_execution_context / get_execution_context` | 结构化运行上下文（供事件发布） |
| `register_extractor / get_extractor` | 全局 MemoryExtractionNode 注册表（ReactLoopNode 初始化时注册，工具层调用） |
| `_dump_assistant_msg` | 将 `LLMResponse` 转为可存 session 的 assistant 消息 dict（含 reasoning_content、tool_calls、thought_signature） |
| `_init_mcp / _cleanup_mcp` | MCP 连接初始化/清理（仅主 agent、MCP 启用时；线程池跑 asyncio 兼容已运行 loop） |
| `run(user_input, session, console, workspace, auto_confirm, execution_context)` | **对外主入口**：设置 thread_local → 写入 user 消息 → `init_context_manager` → 主 agent 初始化 MCP → `GraphRunner().run(...)` → finally 清理 MCP/恢复上下文 |
| `run_sub_agent(task, workspace, agent_id, console, execution_context)` | 启动子 agent：深度+1 → 新建独立 Session（`sub-{agent_id}-{uuid}`）→ 递归调用 `run()` → 发布 `agent.started/completed/failed` 事件 → 恢复 ContextManager，返回 `(结果字符串, 子session)` |

**调用关系**：`run()` → `GraphRunner().run()`（graph/runner.py）→ 内部各节点；`run_sub_agent()` 由 GraphRunner 作为 `run_sub_agent_fn` 回调传入。

### 2.3 `cli.py` — CLI 入口

| 项 | 内容 |
|---|---|
| 职责 | 命令行入口转发 |
| 关键内容 | `from qrclaw.cli.app import main`；`if __name__ == "__main__": main()` |
| 依赖 | `cli/app.py` |

### 2.4 `config.py` — 环境变量配置快照

| 项 | 内容 |
|---|---|
| 职责 | **import 时**调用 `init_config()` + `load_config()` 后，读取环境变量生成模块级常量（即配置的唯一真源在此模块） |
| 关键配置 | 见下表 |

| 配置组 | 常量 | 默认值 |
|---|---|---|
| Agent | `AGENT_NAME` / `MAX_ITERATIONS` | "QRClaw" / 100 |
| LLM | `LLM_PROVIDER`（litellm）/ `LITELLM_API_KEY` / `LITELLM_MODEL` / `LITELLM_BASE_URL` / `LITELLM_API_BASE` / `LITELLM_PROXY_URL` | litellm / "" / gpt-4o / "" |
| 上下文 | `_MODEL_MAX_TOKENS` | 128000 |
| 压缩 | `COMPRESS_THRESHOLD`（60%）/ `COMPRESS_TARGET_MIN_RATIO`(0.20) / `COMPRESS_TARGET_MAX_RATIO`(0.25) / `COMPRESS_SUMMARY_TARGET_TOKENS`(10%) / `COMPRESS_SUMMARY_MAX_TOKENS`(2×) / `COMPRESS_RECENT_TARGET_TOKENS`(12%) / `COMPRESS_RECENT_MAX_TOKENS` | 76800 / 12800 / 15360 / 25600（可覆盖） |
| 搜索 | `TAVILY_API_KEY` | "" |
| 心跳 | `HEARTBEAT_ENABLED` / `HEARTBEAT_INTERVAL` | true / 3600 |
| 日志 | `LOG_LEVEL` / `LOG_MAX_DAYS` / `LOG_TO_FILE` / `LOG_TO_CONSOLE` / `LOG_CONSOLE_LEVEL` | INFO / 30 / true / true / WARNING |
| MCP | `MCP_ENABLED` / `MCP_SERVERS`（JSON 数组） | false / [] |

**调用关系**：被 `prompt.py`（AGENT_NAME）、`agent.py`（MCP_ENABLED/MCP_SERVERS）等 import。

### 2.5 `config_manager.py` — 配置文件管理

| 项 | 内容 |
|---|---|
| 职责 | 管理 `~/.qrclaw/` 下的 YAML 配置（读写、权限保护、注入环境变量） |
| 关键路径 | `CONFIG_DIR = ~/.qrclaw`；`CONFIG_FILE = ~/.qrclaw/config.yaml`（另有 permissions.yaml / MEMORY.md / sessions/ / logs/） |

| 函数 | 作用 |
|---|---|
| `ensure_config_dir` | 建目录并 `chmod 0o700` |
| `_write_config` | 写 YAML，文件权限 `0o600` 保护 API Key |
| `_deep_merge` | 递归深合并默认配置与用户配置 |
| `init_config` | 配置文件不存在时写入 `DEFAULT_CONFIG` |
| `load_config` | 读取 + 深合并 + `_inject_to_env` |
| `_inject_to_env` | 将 YAML 配置 `os.environ.setdefault` 注入环境变量（agent/llm/search/log/heartbeat/compress/mcp 全部小节） |
| `get_config` / `get(key)` | 读取完整配置 / 点号路径取值（如 `llm.model`） |
| `set_config` | 点号路径写值并持久化 |
| `get_config_path` / `get_config_dir` | 返回路径 |

**配置流**：`config_manager.load_config()` → 环境变量 → `config.py` 模块常量 → 全项目引用。

### 2.6 `heartbeat.py` — 心跳机制

| 项 | 内容 |
|---|---|
| 职责 | 定时触发 Agent 维护任务（记忆审查清理、自我反思），通过子 agent 静默执行 |
| 关键类/函数 | 见下表 |

| 成员 | 作用 |
|---|---|
| `Heartbeat`（类） | 后台守护线程管理器：`start(on_trigger)` / `stop()` / `_run_loop()`（`_stop_event.wait(interval)` 循环）/ `_do_heartbeat()`（记录 last_heartbeat、调回调） |
| `get_heartbeat / start_heartbeat / stop_heartbeat` | 全局单例心跳实例管理 |
| `execute_heartbeat_tasks(workspace)` | 读 `workspace.heartbeat_file`（HEARTBEAT.md）→ 构造"静默执行"任务 prompt → `workspace.sub_agent("heartbeat")` 建子工作空间 → `run_sub_agent()` 执行，返回结果摘要 |
| `get_default_heartbeat_content` | 默认 HEARTBEAT.md 内容（记忆维护 + 自我反思） |

**调用关系**：`execute_heartbeat_tasks` → `agent.run_sub_agent`（qrclaw.agent）；Heartbeat 线程 → 回调（一般指向 execute_heartbeat_tasks）。

### 2.7 `llm.py` — LLM 简单兼容层

| 项 | 内容 |
|---|---|
| 职责 | 极薄封装：`chat(messages) -> str`，方便旧代码调用 |
| 关键函数 | `chat(messages: list[dict]) -> str`：委托 `get_llm_service().chat(messages)`，异常向上抛出 |
| 调用关系 | `llm.py → llm_service.py → providers/` |

### 2.8 `llm_service.py` — LLM Service 边界（新代码推荐依赖）

| 项 | 内容 |
|---|---|
| 职责 | 围绕 provider 的薄适配层，新增编排代码应依赖此 service 而非直接 import 全局 provider |
| 关键类 | `LLMService`（见下表） |

| 方法 | 作用 |
|---|---|
| `chat(messages, tools, json_mode, temperature, on_delta)` | 透传 provider.chat，返回 `LLMResponse` |
| `structured(messages, response_model, temperature=0.1, max_retries=3)` | 用 **instructor**（`Mode.MD_JSON`，`create=self.create_openai_like`）做结构化输出解析，返回 Pydantic 模型 |
| `create_openai_like(messages, **kwargs)` | instructor.patch 的适配器，把 LLMResponse 转 OpenAI 风格响应（`_MockOpenAIResponse`） |
| `model_name` | 取 `provider._model` |
| `make_instructor_kwargs` | 兼容桥，provider 不支持则抛 RuntimeError |
| `is_provider_type` | 判断 provider 类型 |
| `get_llm_service()`（模块函数） | 从 `qrclaw.providers` 取全局 provider 实例，包装返回 |

### 2.9 `prompt.py` — System Prompt 构建

| 项 | 内容 |
|---|---|
| 职责 | 仿 OpenClaw 分段式组装 system prompt；工具列表/技能/记忆索引/心跳任务动态注入 |
| 关键函数 | `build_system_prompt(...)`（总入口，见下表） |

| 辅助函数 | 作用 |
|---|---|
| `_build_tooling_section(tool_names)` | 从 `tools.registry.get_schemas()` 动态取工具描述，回退到硬编码 `_TOOL_DESCRIPTIONS`（内置 read_file/write_file/list_directory/web_search/web_fetch/run_shell/write_wiki_page/read_wiki_page/list_wiki_pages/search_wiki/delete_wiki_page/use_skill/spawn_agent/wait_agents 等） |
| `_build_identity_section(agent_file)` | 读 AGENT.md（支持 YAML frontmatter，剥离后注入"## Agent 身份"） |
| `_build_safety_section` | 固定安全边界文案 |
| `_build_workspace_section` | 注入 `platform.system()` + `os.getcwd()` |
| `_build_behavior_section(is_sub_agent)` | 行为准则 + 工具调用风格；**子 agent 追加"任务交付报告"硬核情报规约** |
| `_build_memory_section(memory_dir, wiki_context)` | 注入 Wiki 记忆索引（`WikiMemory.for_workspace(memory_dir).load_index()`）+ 可选 wiki_context（Router LLM 精排注入） |
| `_build_heartbeat_section(heartbeat_file)` | 读 HEARTBEAT.md 注入"## 心跳任务" |
| `_build_skills_section(skill_registry)` | 注入技能列表及 use_skill 使用建议 |

**build_system_prompt 参数**：`heartbeat_file / is_sub_agent / agent_file / skills_dir / memory_dir / wiki_context`；内部自动获取工具列表（get_schemas）与技能注册表（SkillRegistry）。

### 2.10 `workspace.py` — 工作空间

| 项 | 内容 |
|---|---|
| 职责 | 每个 agent 独立工作空间，所有路径由此派生；子 agent 共享父工作空间（轻量一次性） |
| 关键常量 | `AGENTS_ROOT = ~/.qrclaw/agents` |

| 成员 | 作用 |
|---|---|
| `Workspace.__init__(agent_id="default", _root=None)` | root = `AGENTS_ROOT/agent_id`；派生并 mkdir：`sessions_dir`、`logs_dir`、`skills_dir`、`memory_dir`；另有 `heartbeat_file`(HEARTBEAT.md)、`agent_file`(AGENT.md) |
| `get_memory_manager()` | 返回 `WikiMemoryManager.for_workspace(memory_dir)`（Wiki 架构记忆管理器） |
| `sub_agent(sub_id)` | 生成 `agent_id = "{parent}-{sub_id}"`、**共享同一 root** 的新 Workspace（用于 heartbeat、memory-extraction 等一次性子任务） |
| `list_agents()`（模块函数） | 列出 `~/.qrclaw/agents/` 下所有顶级 agent ID |

---

## 3. 模块间调用关系图

```
cli.py ──→ cli/app.py ──→ agent.run()
                             │
        ┌────────────────────┼────────────────────────────┐
        ▼                    ▼                            ▼
  config.py           GraphRunner.run()             agent.run_sub_agent()
  （模块常量）        （graph/runner.py，              │  递归 run()（深度+1）
        ▲             执行引擎）                     ├── 发布 agent.* 事件
        │              ├── prompt.build_system_prompt()   └── 新建 sub Session
config_manager.py     │     ├── tools.registry.get_schemas()
（YAML→环境变量）     │     ├── skills.registry.SkillRegistry
        │             │     └── memory.wiki.WikiMemory
workspace.py ◄───────┤
        │             └── llm_service.get_llm_service()
heartbeat.py ──→ run_sub_agent（心跳任务）           │
                                                  ▼
                                            providers/litellm_provider
                                            （LiteLLM，100+ 提供商）
```

**核心数据流**：
1. 启动：`cli.py → cli/app.py → agent.run()`
2. 配置：`config_manager`(YAML `~/.qrclaw/config.yaml`) → 环境变量 → `config.py` 常量
3. 执行：`agent.run()` 设置 thread_local → `GraphRunner.run()` → 各图节点（react_loop/plan_executor/router/replanner 等）→ 通过 `LLMService` 调 provider
4. 记忆：`workspace.memory_dir` → `WikiMemoryManager` / `ContextManager`（压缩按 60% 阈值触发）
5. 子任务：`run_sub_agent()`（深度隔离）+ `Heartbeat`（定时触发）

---

## 4. 关键架构结论

1. **入口单一**：所有执行都收敛到 `agent.run()`，CLI / 心跳 / 子 agent / MCP 全部复用该入口。
2. **状态隔离**：thread_local（session/workspace/depth/execution_context）+ 全局单例（extractor、mcp_manager、provider）并存，子 agent 并行安全。
3. **LLM 双层抽象**：`llm.py`（str 兼容层）与 `llm_service.py`（LLMResponse + tools + instructor 结构化），新代码应走 service。
4. **Prompt 高度动态**：工具、技能、记忆、心跳、身份（AGENT.md）全部在运行时注入，子 agent 有专属汇报规约。
5. **配置双源**：YAML 文件（持久）+ 环境变量（覆盖），`config.py` 为 import 期快照。
6. **工作空间即资源根**：sessions/logs/skills/memory/HEARTBEAT.md/AGENT.md 全部从 `Workspace` 派生，子 agent 共享 root。
