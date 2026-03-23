# QRClaw 开发计划

> 目标：从零手写一个类 OpenClaw 的自主 AI Agent，边写边学。
> 原则：每次只做一件事，看懂了再做下一步。

---

## 学习路线

```
第一阶段：能跑起来
  → 项目初始化
  → 调通 OpenAI API
  → 实现最简单的对话

第二阶段：让 Agent 会用工具
  → 理解 Tool Calling 机制
  → 实现工具注册系统
  → 写第一个工具（读文件）

第三阶段：ReAct 循环
  → 理解 ReAct 原理
  → 实现推理→工具→观察的循环
  → Agent 能自主完成多步任务

第四阶段：记忆系统
  → 会话历史持久化
  → 重启不丢上下文
  → 中期记忆（Markdown 文件）
  → 心跳机制 + 记忆审查
  → 长期记忆（向量数据库，可选）

第五阶段：扩展（选做）
  → CLI 美化
  → Telegram Bot 接入
  → 更多工具（Shell、网页抓取）

第六阶段：Agent to Agent
  → 子 agent 并行执行
  → 线程隔离（独立 session/workspace）
  → 任务池管理
  → 结果收集与汇总
```

---

## 任务清单

### 第一阶段：能跑起来

- [ ] 项目初始化（pyproject.toml、虚拟环境）
- [ ] 配置管理（读取 .env 中的 API Key）
- [ ] 调用 OpenAI API 完成一次对话
- [ ] 在终端打印出 LLM 的回复

### 第二阶段：工具系统

- [ ] 理解 OpenAI Tool Calling 的请求/响应格式
- [ ] 实现 `@tool` 装饰器注册工具
- [ ] 自动把工具函数转成 LLM 能读懂的 JSON Schema
- [ ] 实现工具执行：LLM 说调哪个就调哪个

### 第三阶段：ReAct 循环

- [ ] 实现 Agent 主循环（最多 N 轮）
- [ ] 每轮：调 LLM → 有工具调用就执行 → 结果塞回上下文 → 继续
- [ ] 没有工具调用时输出最终答案，结束循环
- [ ] 测试：让 Agent 完成「读文件然后总结内容」这种多步任务

### 第四阶段：记忆系统

- [ ] 会话历史存到本地 JSON 文件
- [ ] 启动时加载历史，保持上下文连续
- [ ] 支持清除会话
- [ ] 中期记忆（Markdown 文件）
- [ ] 心跳机制（Heartbeat）
- [ ] 记忆审查工具（review_memory）
- [ ] 长期记忆（向量数据库，可选）

### 第五阶段：扩展（选做）

- [ ] CLI 入口（`qrclaw` 命令启动）
- [ ] 用 `rich` 美化终端输出
- [ ] Telegram Bot 渠道接入
- [ ] 内置工具：写文件、执行 Shell 命令、抓取网页

### 第六阶段：Agent to Agent

- [ ] spawn_agent 工具：后台启动子 agent
- [ ] wait_agents 工具：等待并收集结果
- [ ] 线程隔离：threading.local 隔离 session/workspace
- [ ] 任务池管理：全局任务池 + 锁
- [ ] 子 agent 静默运行：结果返回主 agent
- [ ] 工作空间清理：子 agent 完成后自动清理

---

## 当前进度

> 正在进行：**第四阶段 - 记忆系统（已完成）**

### 第一阶段 ✅
- [x] 创建项目目录结构
- [x] 编写 pyproject.toml
- [x] 创建并激活虚拟环境
- [x] 安装依赖
- [x] 调通 LLM API，能正常对话

### 第二阶段 ✅
- [x] 理解 Tool Calling 数据结构
- [x] 实现工具注册系统（@register 装饰器）
- [x] 实现工具执行（execute）
- [x] 内置工具：read_file / write_file / run_shell

### 第三阶段 ✅
- [x] 实现 ReAct 循环
- [x] finish_reason 标志位判断
- [x] MAX_ITERATIONS 兜底保护

### 第四阶段 ✅
- [x] 会话历史持久化（Session）
- [x] 上下文压缩（摘要策略，target token 10%）
- [x] 压缩阈值可配置（MODEL_MAX_TOKENS）
- [x] 中期记忆（Markdown 文件）
- [x] 心跳机制（Heartbeat）- 定期触发维护任务
- [x] 记忆审查工具（review_memory）- 识别过时/重复内容，支持清理和归档
- [ ] 长期记忆（向量数据库，可选）

### 第五阶段（进行中）
- [x] CLI 入口（qrclaw 命令）
- [x] 工具跨平台适配（pathlib + Windows编码）
- [x] 安全防护（Policy-as-Prompt / 参数校验）
- [x] 日志系统（文件 + 控制台）
- [x] 上下文使用百分比显示
- [x] 配置管理统一（~/.qrclaw/ 目录）
- [x] 日志按会话 ID 分文件存储
- [ ] Telegram Bot 渠道接入

### 第六阶段 ✅
- [x] spawn_agent 工具：后台启动子 agent，立即返回不阻塞
- [x] wait_agents 工具：等待子 agent 完成，收集结果汇总
- [x] 线程隔离：threading.local 隔离 session/workspace
- [x] 任务池管理：全局 _task_pool + threading.Lock
- [x] 子 agent 静默运行：run_sub_agent() 不打印到用户终端
- [x] 自动打印结果：子 agent 完成后结果自动显示
- [x] 工作空间清理：子 agent 完成后清理 sessions/skills/MEMORY.md

---

## 项目结构

```
qrclaw/
├── __init__.py
├── agent.py           # Agent 主循环 + run_sub_agent
├── cli.py             # CLI 入口
├── config.py          # 配置加载（含心跳配置）
├── config_manager.py  # 配置管理
├── llm.py             # LLM 调用
├── prompt.py          # System Prompt 构建
├── workspace.py       # 工作空间管理
├── heartbeat.py       # 心跳机制
├── logger/            # 日志系统
│   ├── __init__.py
│   └── logger.py
├── memory/            # 记忆系统
│   ├── __init__.py
│   ├── session.py     # 短期记忆（会话）
│   ├── compressor.py  # 上下文压缩
│   └── long_term.py   # 中期记忆
├── skills/            # 技能系统
│   ├── __init__.py
│   └── registry.py    # 技能注册
├── tools/             # 工具系统
│   ├── __init__.py
│   ├── registry.py    # 工具注册
│   ├── builtin.py     # 内置工具
│   ├── spawn_agent.py # 启动子 agent
│   ├── wait_agents.py # 等待子 agent
│   ├── review_memory.py # 记忆审查
│   └── skills.py      # 技能工具
└── cli/               # CLI 相关
    ├── __init__.py
    ├── app.py         # 主入口
    ├── input.py       # 输入处理
    ├── display.py     # 显示工具
    └── commands/      # 命令处理
        ├── __init__.py
        ├── agent.py
        ├── session.py
        └── skill.py

~/.qrclaw/agents/<agent_id>/   # 用户数据目录
├── sessions/          # 会话历史
├── logs/              # 日志文件
├── skills/            # 技能
├── MEMORY.md          # 中期记忆
├── HEARTBEAT.md       # 心跳任务配置
└── sub-agents/        # 子 agent 工作空间
```

---

## 心跳机制说明

OpenClaw 风格的记忆维护机制：

### 工作原理

1. **HEARTBEAT.md** - 定义定期执行的维护任务
2. **Heartbeat 线程** - 后台运行，定期触发
3. **review_memory 工具** - AI 审查并清理记忆

### 使用方式

```bash
# 启动时自动创建 HEARTBEAT.md
qrclaw

# 禁用心跳
qrclaw --no-heartbeat

# 配置心跳间隔（环境变量）
HEARTBEAT_INTERVAL=1800  # 30分钟
```

### review_memory 工具

```python
# 分析记忆状态
review_memory(action='analyze')

# 清理过时条目
review_memory(action='cleanup', keep_recent=10)

# 归档旧记忆
review_memory(action='archive', keep_recent=10)
```

### HEARTBEAT.md 示例

```markdown
# Heartbeat 任务

每隔一段时间，Agent 会自动执行以下维护任务。

---

## 记忆维护

- 审查 MEMORY.md，清理过时条目
- 合并重复信息
- 保留用户偏好

**建议工具**: review_memory(action='analyze')
```