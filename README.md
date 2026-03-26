# QRClaw: 运行在本地的自主 AI Agent 🐾

[![PyPI version](https://badge.fury.io/py/qrclaw.svg)](https://badge.fury.io/py/qrclaw)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**QRClaw** 是一个开源的、可本地运行的自主 AI Agent 框架。它不仅拥有强大的记忆系统，还内置了多引擎联网搜索能力，支持插件扩展，能够像人类一样规划任务并逐步执行。

## 🌟 核心特性

- **🚀 本地优先**：核心逻辑完全在本地运行，数据隐私安全。
- **🤖 多模型支持**：
  - **OpenAI 兼容接口**：DeepSeek, Moonshot, Qwen, GPT-4o...
  - **Google Vertex AI**：原生支持 Gemini Pro，速度快，上下文长，**仅需 API Key**。
- **🔍 智能联网搜索**：
  - **多引擎支持**：Tavily (强)、Google (准)、DuckDuckGo (免费保底)。
  - **自动故障转移**：Tavily 额度用完？自动切 DuckDuckGo，永不断连。
  - **结果清洗**：自动去除广告，提取纯净正文，拒绝垃圾信息。
- **🧠 记忆系统**：
  - **长期记忆 (Long-Term)**：自动记录你的偏好、项目背景。
  - **短期会话 (Session)**：支持多轮对话，上下文自动管理。
- **🛠️ 插件化架构**：
  - **Tools**：文件操作、Shell 命令、网页抓取。
  - **Skills**：一键安装社区技能（如代码审查、文档生成）。
- **📅 任务规划**：遇到复杂问题，自动拆解步骤（Plan -> Execute -> Review）。

## 📦 快速安装

```bash
# 推荐使用 pip 安装
pip install qrclaw
```

或者从源码安装：

```bash
git clone https://gitee.com/fu-qingrong/qrclaw.git
cd qrclaw
pip install .
```

## 🚀 快速上手

1. **初始化**：
   在任意目录下运行 `qrclaw`，它会自动引导你配置。

   ```bash
   qrclaw
   ```

2. **基本命令**：
   - **对话**：直接输入自然语言（例如："帮我查一下最新的 RAG 优化方案"）。
   - **多行输入**：按 `Alt+Enter` 换行。
   - **退出**：输入 `exit`。
   - **管理 Agent**：`/agent list`。
   - **管理会话**：`/session list`。

## ⚙️ 配置指南

QRClaw 会自动读取环境变量（`.env`）或系统环境变量。

### 模型配置 (二选一)

#### 方案 A: OpenAI 兼容接口 (推荐 DeepSeek/Moonshot)
```bash
export LLM_PROVIDER="openai"                        # 默认值
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.deepseek.com"   # 如果用 DeepSeek
export OPENAI_MODEL="deepseek-chat"                 # 模型名称
```

#### 方案 B: Google Vertex AI (Gemini Pro)
**无需安装 gcloud 或配置 Project ID**，只需一个 API Key。

```bash
export LLM_PROVIDER="vertex"
export VERTEX_API_KEY="AIza..."                     # 你的 Google API Key
export OPENAI_MODEL="gemini-1.5-pro"                # 可选，默认 gemini-pro
```

### 联网搜索配置（可选）
QRClaw 默认使用 **DuckDuckGo**（完全免费）。如果你想更强：

```bash
# 方案 A: Tavily (推荐，不仅能搜还能读网页)
export TAVILY_API_KEY="tvly-..."

# 方案 B: Google Search (每日 100 次免费)
export GOOGLE_API_KEY="AIza..."
export GOOGLE_CSE_ID="012345..."
```

## 🧩 插件开发

想给 Agent 加个新能力？只需在 `qrclaw/tools/` 下新建一个 `.py` 文件：

```python
from qrclaw.tools.registry import register

@register(description="计算两个数的和")
def add(a: int, b: int) -> int:
    return a + b
```

重启 `qrclaw`，Agent 就能用这个新技能了！

## 🤝 贡献指南

欢迎提交 PR 或 Issue！

1. Fork 本仓库
2. 创建分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送 (`git push origin feature/AmazingFeature`)
5. 提 Pull Request

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源。允许个人或商业免费使用，只需保留版权声明。
