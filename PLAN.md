# JavaClaw 开发计划

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

第五阶段：扩展（选做）
  → CLI 美化
  → Telegram Bot 接入
  → 更多工具（Shell、网页抓取）
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

### 第五阶段：扩展（选做）

- [ ] CLI 入口（`javaclaw` 命令启动）
- [ ] 用 `rich` 美化终端输出
- [ ] Telegram Bot 渠道接入
- [ ] 内置工具：写文件、执行 Shell 命令、抓取网页

---

## 当前进度

> 正在进行：**第五阶段 - 扩展**

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

### 第五阶段（进行中）
- [x] CLI 入口（javaclaw 命令）
- [x] 工具跨平台适配（pathlib + Windows编码）
- [x] 安全防护（Policy-as-Prompt / 参数校验）
- [ ] Telegram Bot 渠道接入
