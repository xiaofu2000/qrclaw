# QRClaw

QRClaw 是一个本地 AI Agent。它提供交互式 CLI、Web UI、OpenAI 兼容 API、工具调用和跨会话 Wiki 记忆。默认直接执行任务，启用 Plan 后由路由器选择直接执行或分步执行。

## 从源码运行

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)；修改 Web UI 源码还需要 Node.js 和 npm。

```bash
uv sync --extra dev
uv run qrclaw
```

首次运行会创建 `~/.qrclaw/config.yaml`。在该文件的 `llm` 部分填写 `api_key`、`model` 和需要时的 `base_url`，例如：

```yaml
llm:
  api_key: "你的 API Key"
  model: "openai/deepseek-flash"
  base_url: "https://api.deepseek.com"
```

CLI 使用 `/agent`、`/session`、`/skill` 管理对应资源，输入 `exit` 退出。CLI 默认自动执行工具调用，包括 Shell 与文件写入，请仅在信任任务来源的环境中运行。

## Web UI 和 API

运行 `uv run qrclaw serve`，打开 `http://127.0.0.1:8765` 即可使用随包提供的 Web UI。

修改前端时，在源码目录运行 `./start_ui.sh`，脚本会启动本地 API 和 Web 开发服务器；打开终端显示的 Vite 地址。也可以分别运行：

```bash
uv run qrclaw serve
npm --prefix web ci
npm --prefix web run dev
```

API 默认只监听 `127.0.0.1:8765`，提供 `/health`、`/v1/models`、`/v1/sessions`、`/v1/chat/completions`。聊天接口使用模型名 `qrclaw-agent`：默认返回完整 JSON，传入 `"stream": true` 时返回 OpenAI 风格的 SSE 片段。`GET /v1/sessions/{session_id}/events/stream?after=-1` 按序号推送计划和工具状态，断线后可用最后收到的事件序号续接。Web UI 开发服务器通过 `/api` 代理到本地 API。

执行 `npm --prefix web run build` 会更新 `qrclaw/server/static` 中的发布资源；Python 包和开发模式共用 `web/src` 中的界面。任务运行状态与错误由后端维护，刷新页面会重新获取；草稿在当前页面按会话分别保留。执行过程可通过右上角展开查看。

输入框上方提供 Plan 模式开关，默认关闭，选择保存在当前浏览器并用于下一次发送及编辑重跑。关闭时跳过路由判断、自动计划与重规划调用。CLI 和未指定开关的 API 使用 `~/.qrclaw/config.yaml` 的 `agent.plan_enabled`（默认 `false`），也可设置环境变量 `PLAN_ENABLED=true`；聊天请求可用 `plan_enabled` 覆盖该默认值。

会话详情返回 `context_tokens`、`context_limit` 和 `compression_threshold`。界面显示当前上下文估算及窗口占比，首次执行后计入系统提示词和工具定义；这与累计消耗的 `total_tokens` 不同。默认窗口为 485,000 tokens，超过窗口的 90%（436,500 tokens）时尝试自动压缩，预留 10% 给后续消息和回复。压缩中、完成、失败或跳过状态由 `compression` 事件实时推送，顶部、输入区和执行面板都能看到；完成提示包含压缩前后用量，刷新后保留。用量依据本地分词器估算，窗口上限来自 `llm.max_tokens` 配置，触发比例来自 `compress.threshold_ratio`（可用环境变量 `MODEL_MAX_TOKENS`、`COMPRESS_THRESHOLD_RATIO` 覆盖）。配置窗口应与实际使用的模型容量一致。

主聊天会话的 `events` 会记录每次子 Agent 启动及完成状态，事件 `meta.child_session_id` 指向独立保存的子会话。Web UI 在主聊天中显示可点击的子任务卡片，右侧执行面板读取该子会话的完整消息与工具结果；切换面板不会切换主聊天。会话详情的 `active_subagents` 标记后台是否仍有子任务；子会话也可通过现有会话详情与事件流接口读取，运行中的可见文字由事件流的 `live_text` 帧推送。删除父会话时会清理它关联的子会话。

修改 Python 后端代码后需要重启 `qrclaw serve`，更新前端发布资源后需要刷新浏览器页面；已运行的服务不会自动加载新代码。

如果需要让 API 监听非本机地址，先设置 `QRCLAW_API_TOKEN`；远程请求须携带 `Authorization: Bearer <token>`。API 聊天也会自动执行工具调用，应妥善保管 token。通过反向代理开放服务时，应在代理处设置访问控制。

兼容旧的 OpenWebUI 启动方式：`uv run python api_server.py` 在 `127.0.0.1:8000` 提供同一 API，并继续使用 `api-server` Agent 的原有数据目录。将 OpenWebUI 的 OpenAI 兼容接口地址设置为 `http://127.0.0.1:8000/v1`，模型选择 `qrclaw-agent`。

## Wiki 记忆更新

自动记忆按会话累计用户和助手正文，不提取工具结果、工具调用消息及参数、系统提示词、子任务执行报告或压缩摘要；触发 token 统计也使用同样的过滤规则。助手最终回复中引用的工具结论仍属于对话正文，由模型判断是否值得长期保存。

默认累计至少 3 轮完整对话后，闲置 2 分钟再批量提取；新请求到来时延后计时。长对话中，未处理正文达到 5000 个估算 token 时在主请求结束后提取，不必等待闲置。两次自动提取至少间隔 5 分钟，失败重试也遵守冷却。`write_wiki_page` 主动记忆立即执行，不受自动阈值限制。

未处理原文、消息版本和已处理进度保存在各工作空间的 `memory/.extractions/<session-id>.json`，同一会话合并为一个待处理批次。模型判定无需更新时也记录进度；服务重启会恢复待处理任务。压缩前保存尚未提取的原文，避免丢失内容或重复分析摘要；编辑、删除消息时撤销对应待处理版本，删除会话时清理其进度。尚未升级过的旧会话没有提取进度，首次恢复可能需要分析一次当前正文。

可用环境变量调整：`MEMORY_MIN_TURNS`（默认 3）、`MEMORY_IDLE_SECONDS`（120）、`MEMORY_COOLDOWN_SECONDS`（300）、`MEMORY_INIT_THRESHOLD`（5000，首次长对话阈值）、`MEMORY_UPDATE_INTERVAL`（5000，后续长对话阈值）。修改后重启服务生效；这些 token 按正文字符数除以三估算，不是模型实际账单用量。

自动提取和 `write_wiki_page` 共用写入流程：新页面直接创建；已有页面先读取旧正文，再由模型结合新增信息整理，替换明确过期的内容并保留有效信息。模型生成完整的新标签和关联集合，允许移除过期项；正文、索引及操作日志同步更新，没有实际变化时跳过保存。

模型合并失败、返回空正文，或合并期间页面被修改、删除时，不覆盖旧页面。自动提取任务会保留在队列中等待后续重试，主动写入会报告失败。每个需要更新的已有页面会增加一次合并模型调用；不批量重写已有记忆。

页面保存实际来源 ID、版本及批次回执；`memory/.wiki-state.json` 保存来源版本、独立历史基线和整理队列。来源只允许引用本次输入的真实 ID，且需用户正文支持，助手推测不能独立建立用户事实。维护约定保存在工作空间的 `memory/SCHEMA.md`，首次创建后不会覆盖用户修改。

聊天消息编辑后撤销旧版本，删除消息或会话时清除对应来源原文。受影响记忆立即停止供 Agent 检索，闲置后按有效来源重建；这类整理无需累计三轮。无有效来源且无历史基线的页面退出检索；手动编辑生成独立的用户确认来源，移除的事实不会从早期批次补回，新的明确纠正仍可更新。主动删除页面会取消整理，并禁止旧任务恢复它。进度与页面分别原子保存，写页后确认前重启会通过页面回执跳过已完成的写入。

### Web 记忆管理

打开侧栏“记忆”或访问 `http://127.0.0.1:8765/#/memory`，支持搜索分页、Markdown 正文、来源会话跳转、修改差异、编辑删除、规则检查和整理重试。草稿按页面保留，保存和删除携带版本；遇到 409 时保留草稿，查看最新内容后再明确保存。后台任务存在时每 2 秒查询，隐藏或离开页面停止查询；断线不会自动重发写入。切换记忆页面保留当前聊天草稿和聊天任务。

接口位于 `/v1/wiki`：`GET /pages`、`GET /pages/{name}`、`PATCH /pages/{name}`、`DELETE /pages/{name}?revision=版本`、`GET /status`、`POST /lint`、`POST /pages/{name}/repair`。本机访问限制与聊天接口相同；远程访问需要 `QRCLAW_API_TOKEN`。

主 Agent 在 Direct 和 Plan 中均可使用 `list_wiki_pages`、`search_wiki`、`read_wiki_page`、`lint_wiki`。搜索最多 5 项，Plan 精选最多 20 个候选摘要；正文单次约 2000 token、单轮约 8000 token，按版本和范围去重，并返回续读位置、来源状态及反向引用。普通聊天搜索不额外调用检索模型，Plan 沿用原有筛选模型。规则检查不调用模型；仅用户明确要求时通过 `lint_wiki(semantic=true)` 检查疑似语义冲突，不自动删除页面。

旧 Markdown 页面继续可读，并标记“来源未追溯”。首次关联来源前保存独立旧基线，历史来源无法自动还原，聊天删除不能精确撤销这部分旧记忆。自动纠错依赖模型判断，不等于事实审定；不确定的冲突保留为“待确认”。

## MCP 工具按需发现

在 `~/.qrclaw/config.yaml` 中配置 MCP 服务即可启用，无需另启动搜索代理：

```yaml
mcp:
  servers:
    - label: my-service
      transport: stdio
      command: python
      args: ["/绝对路径/mcp_server.py"]
      # description: "查询订单、库存和物流；处理订单状态或库存问题时使用。"  # 可选：能力和使用时机
      # include_tools: [read_data, update_data]  # 可选：允许搜索和调用的工具
      # exclude_tools: [delete_data]             # 可选：排除工具，优先于白名单
```

QRClaw 用 FastMCP 在进程内保存允许使用的工具目录。模型每轮只获得 `mcp__search_tools` 和 `mcp__call_tool` 两个 MCP 入口：任务匹配已连接服务的本地或远程能力时，用 `query` 描述用途，FastMCP 的 BM25 对整个目录按相关性排序，最多返回 5 个工具及完整 `inputSchema`，再按该定义调用。例如先搜索 `mcp__search_tools(query="echo text")`，再用 `mcp__call_tool(name="my-service__echo", arguments={"text": "hello"})` 执行。普通聊天不自动搜索，发现的工具不会追加注册到后续请求的工具列表。搜索参数由旧版正则 `pattern` 改为 `query`，调用方应同步更新。

系统提示会介绍实际连接且有可用工具的服务：优先采用配置中的 `description`，否则使用服务初始化返回的 `instructions`，每个服务最多保留 600 字符。轻量目录列出**所有允许工具**的名称和简短用途（每项最多 80 字符），不再按工具数量截断，也不包含参数定义；完整描述和参数按需搜索。目录长度随工具数量增长，但不会加载全部 schema。系统提示要求模型在任务匹配这些能力时主动搜索专用工具，无需用户点名；实际选择仍由模型决定。不要求安装任何特定服务，也不会常驻某个服务的工具定义。当前 BM25 使用英文分词，中文任务先转换成目录里的英文用途词或工具名；它不提供跨语言语义检索。

工具名称带服务标签前缀，避免跨服务同名冲突；服务标签必须唯一。`include_tools`/`exclude_tools` 是用户在上述配置文件中选择的可选项，代码不内置任何服务的工具名单：白名单表示只允许列出的工具，黑名单表示排除列出的工具，黑名单优先。未设置白名单时允许所有未被黑名单排除的工具；显式空白名单禁用全部工具。名单使用服务原始工具名，同时限制能力目录、搜索和调用。它们用于限制可用范围，不是按需加载的必要条件。连接时仍读取完整目录（含分页），stdio/SSE 配置、工具执行和连接清理沿用原有流程。子 Agent 继续遵守现有工具白名单。

## 工作空间与沙箱

Agent 数据存放在 `~/.qrclaw/agents/<agent-id>/`。默认 Agent 不启用 Docker 沙箱；其他 Agent 依配置启用沙箱。启用时需要 Docker，容器启动或执行失败会返回错误，不会改在宿主机执行；文件工具限制在对应工作空间内。CLI 和本机 API 的自动执行设置与沙箱开关是两件独立的事。

## 检查

```bash
uv run --no-sync pytest -q
npm --prefix web run lint
npm --prefix web test
npm --prefix web run build
```

开源协议见 [LICENSE](LICENSE)。
