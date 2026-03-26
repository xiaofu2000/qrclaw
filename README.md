# QRClaw

<div align="center">

[![PyPI version](https://badge.fury.io/py/qrclaw.svg)](https://badge.fury.io/py/qrclaw)
[![Python](https://img.shields.io/pypi/pyversions/qrclaw.svg)](https://pypi.org/project/qrclaw/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**A powerful AI agent with skills system**

[English](#english) | [中文](#中文)

</div>

---

## English

### Features

- 🤖 **AI Agent** - Powered by OpenAI, with intelligent context management
- 🎯 **Skills System** - Extend functionality with modular skills
- 🔄 **OpenClaw Compatible** - Fully compatible with OpenClaw skills ecosystem
- 💾 **Session Management** - Persistent conversation history
- 🎨 **Rich UI** - Beautiful terminal interface with syntax highlighting
- 📦 **Easy to Use** - Simple installation and configuration

### Installation

```bash
# Install from PyPI
pip install qrclaw

# Or install from GitHub
pip install git+https://github.com/fu-qingrong/qrclaw.git
```

### Quick Start

1. **Set up API Key**
   ```bash
   # Create .env file
   echo "OPENAI_API_KEY=your-api-key-here" > ~/.qrclaw/.env
   ```

2. **Run QRClaw**
   ```bash
   qrclaw
   ```

3. **Start Chatting**
   ```
   > Hello, how can you help me?
   > Analyze this project for me
   > Review the code in src/main.py
   ```

### Skills System

QRClaw comes with built-in skills:

- **review-code** - Review code and provide improvement suggestions
- **generate-docs** - Generate README, API docs, etc.
- **analyze-project** - Analyze project structure and code quality
- **install-skill** - Install new skills from GitHub/ClawHub

#### Install New Skills

```bash
# In QRClaw chat
> 导入 skill meow-finder

# Or use CLI
/skill import meow-finder
```

### Configuration

Configuration file: `~/.qrclaw/config`

```ini
[DEFAULT]
model = gpt-4o-mini
max_tokens = 128000
log_level = INFO
```

### Commands

- `exit` - Exit QRClaw
- `clear` - Clear conversation history
- `/skill list` - List installed skills
- `/skill import <name>` - Import a new skill

### Development

```bash
# Clone repository
git clone https://github.com/fu-qingrong/qrclaw.git
cd qrclaw

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .

# Run
python -m qrclaw.cli
```

---

## 中文

### 特性

- 🤖 **AI 代理** - 基于 OpenAI，智能上下文管理
- 🎯 **技能系统** - 模块化技能扩展功能
- 🔄 **OpenClaw 兼容** - 完全兼容 OpenClaw 技能生态
- 💾 **会话管理** - 持久化对话历史
- 🎨 **精美界面** - 终端界面美观，支持语法高亮
- 📦 **易于使用** - 简单安装和配置

### 安装

```bash
# 从 PyPI 安装
pip install qrclaw

# 或从 GitHub 安装
pip install git+https://github.com/fu-qingrong/qrclaw.git
```

### 快速开始

1. **设置 API Key**
   ```bash
   # 创建 .env 文件
   echo "OPENAI_API_KEY=your-api-key-here" > ~/.qrclaw/.env
   ```

2. **运行 QRClaw**
   ```bash
   qrclaw
   ```

3. **开始对话**
   ```
   > 你好，你能帮我做什么？
   > 帮我分析一下这个项目
   > 审查 src/main.py 的代码
   ```

### 技能系统

QRClaw 内置技能：

- **review-code** - 代码审查和改进建议
- **generate-docs** - 生成 README、API 文档等
- **analyze-project** - 分析项目结构和代码质量
- **install-skill** - 从 GitHub/ClawHub 安装新技能

#### 安装新技能

```bash
# 在 QRClaw 对话中
> 导入 skill meow-finder

# 或使用 CLI
/skill import meow-finder
```

### 配置

配置文件：`~/.qrclaw/config`

```ini
[DEFAULT]
model = gpt-4o-mini
max_tokens = 128000
log_level = INFO
```

### 命令

- `exit` - 退出 QRClaw
- `clear` - 清除对话历史
- `/skill list` - 列出已安装技能
- `/skill import <name>` - 导入新技能

### 开发

```bash
# 克隆仓库
git clone https://github.com/fu-qingrong/qrclaw.git
cd qrclaw

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -e .

# 运行
python -m qrclaw.cli
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgments

- Inspired by [OpenClaw](https://github.com/openclaw/openclaw)
- Built with [OpenAI API](https://openai.com/)
- UI powered by [Rich](https://github.com/Textualize/rich)