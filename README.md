# QRClaw: 一个先进的自主 Agent 框架

QRClaw 是一个先进的、运行在本地的自主 AI 框架，专为执行复杂任务而设计。其核心设计强调智能规划、操作安全和极致效率。与简单的 Agent 不同，QRClaw 利用其先进的架构来动态应对不同复杂度的挑战。

## 核心架构与设计哲学

QRClaw 的强大源于一系列独特且深度集成的架构组件：

### 🚀 分层式智能执行图 (Hierarchical Smart Execution Graph)
这是 QRClaw 的“大脑”。它摒弃了单一的 ReAct 循环，采用了一个智能的、多层次的执行图：
- **路由节点 (RouterNode)**: 首先评估任务的复杂度。
- **条件执行**:
    - **简单任务**: 由一个标准的 `ReactLoopNode` 高效处理。
    - **复杂任务**: 委托给 `PlanExecutorNode`，它会分解问题，然后编排多个子 Agent 进行并行或串行执行。

### 💡 上下文优化的动态技能系统 (Context-Optimized Dynamic Skill System)
为了最大化 Token 使用效率，QRClaw 设计了独特的两阶段技能加载机制：
1.  **轻量级发现**: 首先，只向 LLM 提供所有可用技能的轻量级描述。
2.  **按需加载 (Just-in-Time Loading)**: 只有当 LLM 决定使用某个技能时，该技能的完整文档和规范才会被加载到上下文中。这种“即时加载”策略极大地节省了成本和宝贵的上下文窗口空间。

### 🛡️ 隔离且灵活的安全沙箱 (Isolated & Flexible Secure Sandbox)
安全是最高优先级。所有代码和 Shell 命令都在一个隔离的容器化沙箱（如 Docker）中执行，防止对主机系统产生任何影响。沙箱支持高度可配置，同时会阻止访问敏感路径，并在容器环境不可用时优雅地降级为本地执行。

### ⚡ 并行能力与清晰的状态管理 (Parallelism & Clean State Management)
QRClaw 为并发而生。它可以并行运行多个子 Agent 来处理复杂计划的不同部分。一个清晰的生命周期管理系统使用 `threading.local` 来确保每个 Agent 都在其隔离的状态下运行，从而避免冲突并确保执行的可预测性。

## 快速上手

### 1. 安装

**通过 pip 安装 (推荐)**:
```bash
pip install qrclaw
```

**从源码安装**:
```bash
git clone https://github.com/your-repo/qrclaw.git
cd qrclaw
pip install .
```

### 2. 初始化与配置
首次运行，请先初始化 Agent。这会自动创建所需的工作区和配置文件。
```bash
qrclaw
```
然后，根据提示在 `.env` 文件中配置您的 LLM Provider 和 API Keys。

### 3. 开始交互
直接运行 `qrclaw` 与 Agent 对话：
```
$ qrclaw
欢迎来到 QRClaw!
> 你好
...
```

- **多行输入**: 输入 `"""` 或 `---` 开始多行模式，再次输入 `"""` 或 `---` 结束。
- **管理 Agent**: 使用 `qrclaw agent list`、`qrclaw agent create <name>` 等命令管理多个 Agent。
- **管理会话**: 使用 `qrclaw session start`、`qrclaw session list` 等命令进行会话管理。

## 配置

QRClaw 通过环境变量进行配置。您可以创建一个 `.env` 文件放在项目根目录。

- **LLM 提供商**:
  ```
  LLM_PROVIDER=openai  # 或 vertex_ai
  ```
- **OpenAI**:
  ```
  OPENAI_API_KEY="sk-..."
  OPENAI_MODEL="gpt-4-turbo"
  ```
- **Google Vertex AI**:
  ```
  VERTEX_API_KEY="your-google-cloud-project-id"
  VERTEX_LOCATION="us-central1"
  ```
- **联网搜索**:
  ```
  # Tavily 搜索 API
  TAVILY_API_KEY="tvly-..."

  # Google 自定义搜索引擎 API
  # GOOGLE_API_KEY="AIza..."
  # GOOGLE_CSE_ID="..."
  ```

## 技能开发 (Plugin Development)

通过创建 `skills/your_skill/SKILL.md` 来扩展 Agent 的能力。系统会自动发现并加载。

**示例: `skills/calculate/SKILL.md`**
```markdown
---
name: calculate
description: "计算一个数学表达式的结果。示例: 2 * (3 + 4)"
entry_point: "main"
args:
  expression: "要计算的数学表达式，例如：'2+2'"
---

## Tool Code
'''python
import ast
import operator as op

# 支持的运算符
operators = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
    ast.Div: op.truediv, ast.Pow: op.pow, ast.BitXor: op.xor,
    ast.USub: op.neg
}

def eval_expr(expr):
    """
    安全地计算一个数学表达式字符串。
    """
    return eval_(ast.parse(expr, mode='eval').body)

def eval_(node):
    if isinstance(node, ast.Num):  # <number>
        return node.n
    elif isinstance(node, ast.BinOp):  # <left> <operator> <right>
        return operators[type(node.op)](eval_(node.left), eval_(node.right))
    elif isinstance(node, ast.UnaryOp):  # <operator> <operand> e.g., -1
        return operators[type(node.op)](eval_(node.operand))
    else:
        raise TypeError(node)

def main(expression: str):
    """
    计算一个数学表达式的结果。
    """
    result = eval_expr(expression)
    print(f"'{expression}' 的计算结果是 {result}")
    return result
'''
```

## 贡献
我们欢迎任何形式的贡献！请提交 Pull Request 或创建 Issue。

## 开源协议
本项目基于 [MIT License](LICENSE) 开源。
