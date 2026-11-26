# QRClaw 记忆与 Agent 效果回归评测集（`evals/`）

`evals/` 是直接驱动 QRClaw 真实业务模块（`MemoryExtractionNode`、`WikiMemory`、`WikiProvenance`、`WikiService`、`Compressor`、`RouterNode`、`GraphRunner`、`ReactLoopNode` 及文件系统工具链路）的双模式回归评测体系。

所有评测用例均在独立的临时 `QRCLAW_HOME` 工作空间中执行，**不会读写或污染本机 `~/.qrclaw` 中的真实会话与长期记忆**。

---

## 一、快速开始

### 1. 离线确定性回归门禁（默认，无需 API Key，秒级完成）

离线模式通过继承 `LiteLLMProvider` / `LLMProvider` 的高保真脚本化模型 `ScriptedEvalProvider` 驱动真实 `instructor` 结构化解析、来源追溯校验、上下文压缩与多步工具调用闭环，适合本地提交前自检与 GitHub Actions CI 门禁：

```bash
# 运行全部 4 大维度 20 个工作台场景，在终端打印 Rich 汇总表
uv run python -m evals

# 开启 CI 分数门禁（低于阈值时退出码为 1）
uv run python -m evals --mode offline --fail-under 0.95
```

### 2. 在线真实大模型对比评测（`--mode live`）

修改提取/合并/路由/摘要 Prompt、升级记忆整理逻辑或切换不同模型（如 DeepSeek、MiniMax、Claude、GPT）时，可直接使用本机 `~/.qrclaw/config.yaml`（或环境变量 `LITELLM_MODEL`、`LITELLM_API_KEY`、`LITELLM_BASE_URL`）运行真实模型跑分对比：

```bash
# 使用当前配置的真实大模型跑全量评测并导出 JSON 报告
uv run python -m evals --mode live --output evals/reports/latest.json

# 仅针对“记忆提取”维度进行快速验证
uv run python -m evals --mode live --category memory_extraction --output evals/reports/memory_ext.json

# 仅针对单条场景调试 Prompt
uv run python -m evals --mode live --case ctx_comp_03_summary_fact_retention

# 临时覆盖模型对比跑分（例如对比两个不同模型）
LITELLM_MODEL="deepseek/deepseek-chat" uv run python -m evals --mode live --output evals/reports/deepseek.json
LITELLM_MODEL="openai/gpt-4o" uv run python -m evals --mode live --output evals/reports/gpt4o.json
```

> `evals/reports/` 已加入 `.gitignore`，生成的对比报告不会污染 Git 工作区。

---

## 二、四大评测维度（共 20 个场景）

| 数据集文件 | 维度标识 (`--category`) | 场景数 | 核心覆盖能力 |
|---|---|---:|---|
| `datasets/memory_extraction.json` | `memory_extraction` | 6 | 编码与语言偏好提取及用户 `source_id` 绑定、推翻旧配置原地合并更新、闲聊/偶发报错 `skip`、拦截无用户支撑的助手臆测、`filter_memory_messages` 工具日志与摘要过滤、`write_wiki_page` 立即写入与待处理批次去重 |
| `datasets/memory_provenance_and_retrieval.json` | `memory_provenance_and_retrieval` | 5 | 关键词与语义联合搜索、`read_wiki_page` 分页续读与重复范围去重、删改消息后即时检索隔离与原文抹除、`manual` 手动确认来源抗早期会话删除、删除页面墓碑防旧批次复活、`WikiService.lint()` 断链/孤立页/来源状态检查 |
| `datasets/context_compression.json` | `context_compression` | 4 | 超阈值递归压缩合并旧 `[SUMMARY]`、保留窗口不拆散成对 `tool_calls`/`tool`、压缩前未提取原文转交记忆队列（知识零丢失）、四段式摘要关键事实保留率评分、并发写冲突自动放弃覆盖 |
| `datasets/agent_routing_and_tools.json` | `agent_routing_and_tools` | 5 | Plan 开启下简单问答 Direct 与复杂多阶段并行/串行 DAG 路由、Plan 关闭跳过 Router、ReAct `read_file` → `patch` → 复核闭环、工具参数失配容错自愈、沙箱越界读取与 `/etc` 敏感路径写入拦截 |

---

## 三、如何新增评测用例

1. **添加数据集条目**：在 `evals/datasets/<category>.json` 的 `cases` 数组中新增用例对象，确保 `id` 全局唯一（小写字母、数字与下划线），并填写清晰的 `title`、`scenario`、`weight` 与 `description`。
2. **绑定执行与评分逻辑**：
   - 若复用已有 `scenario`（例如测试新的偏好对话或新的四段式摘要文本），无需改代码即可直接生效；
   - 若引入全新的交互链路，在 `evals/runner.py` 的 `EvalRunner._dispatch` 中注册新 `scenario` 处理函数，使用 `evals/scorers.py` 中的 `bool_check` / `ratio_check` 返回检查项，并在 `evals/scripted_provider.py` 中补充对应的离线确定性响应。
3. **运行回归验证**：
   ```bash
   uv run --no-sync pytest qrclaw/tests/test_eval_harness.py -q
   uv run --no-sync python -m evals --mode offline --fail-under 1.0
   ```
