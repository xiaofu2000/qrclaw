# QRClaw 项目架构分析报告

> 本报告基于对 qrclaw v0.9.0 源码的全面分析，综合了 agent.py、tools、memory、graph、sandbox、providers、cli、config、skills、workspace、prompt 等核心模块的深度解读。

---

## 1. 整体架构概览

### 1.1 模块关系图

```
┌──────────────────────────────────────────────────────────────────┐
│                         CLI 入口 (cli/app.py)                     │
│              argparse → Workspace → Session → 主循环               │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ 调用
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Agent 核心编排 (agent.py)                      │
│           run() / run_sub_agent() + thread_local 状态隔离          │
└──────────┬─────────────────────────────┬──────────────────────────┘
           │ 委托执行                    │ 委托执行
           ▼                            ▼
┌───────────────────────┐    ┌──────────────────────────────────┐
│   GraphRunner (runner) │    │     ContextManager (单例)          │
│   任务执行图入口        │    │  System Prompt 缓存 + Dirty Flag  │
│   ├─ RouterNode       │    └──────────────────────────────────┘
│   ├─ ReactLoopNode    │                          ▲
│   ├─ PlanExecutorNode  │                          │ 组装 messages
│   └─ ReplannerNode     │    ┌──────────────────────────────────┐
└──────────┬──────────────┘    │      LLM Provider (providers/)   │
           │ 工具调用          │      ├─ base.py (ABC 接口)        │
           ▼                  │      └─ litellm_provider.py       │
┌───────────────────────┐      │           (统一调用 100+ 模型)     │
│  Tool Registry        │      └───────────────┬──────────────────┘
│  (tools/registry.py)  │                      │ chat()
│  @register 装饰器      │                      ▼
│  ├─ filesystem        │              ┌───────────────┐
│  ├─ shell (沙箱双模式) │              │ 外部 LLM API   │
│  ├─ web               │              │ (OpenAI/Claude/│
│  ├─ memory_tools      │              │  DeepSeek...)  │
│  ├─ wiki_tools        │              └───────────────┘
│  ├─ agent_tools       │
│  ├─ spawn_agent       │
│  └─ wait_agents       │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                        Sandbox 隔离层 (sandbox/)                  │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ ConfigManager│  │ SandboxManager  │  │  ContainerManager    │  │
│  │  (permissions│  │ (容器生命周期,  │  │  (Docker CLI 封装,   │  │
│  │   .yaml 单例)│  │   缓存+降级)   │  │   挂载+安全验证)     │  │
│  └──────────────┘  └────────────────┘  └──────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                        记忆系统 (memory/)                         │
│  ┌────────────┐  ┌────────────────┐  ┌────────────────────────┐ │
│  │  Session   │  │ ContextManager  │  │   MemoryManager         │ │
│  │ (短期/JJSON)│  │ (统一上下文,    │  │   (中期/YAML+MD,        │ │
│  │            │  │  thread-local) │  │    frontmatter)         │ │
│  └────────────┘  └────────────────┘  └────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    WikiMemory (长期/智能拆分)                │ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                     Skills 系统 (skills/registry.py)              │
│  SkillRegistry (单例) → load_from_dir() → SKILL.md 解析           │
│  轻量级描述注入 System Prompt / 完整内容按需加载                   │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 调用链路

```
用户输入
  └─> cli/app.py: main() → run(user_input, session, ...)
        └─> agent.py: run() 
              ├─> init ContextManager (thread-local 单例)
              └─> GraphRunner.run()
                    ├─> RouterNode (复杂度判断)
                    │     └─> LLM (JSON_MODE + instructor Pydantic)
                    ├─> ReactLoopNode (简单任务)
                    │     ├─> ContextManager.get_messages()
                    │     ├─> LLMProvider.chat()
                    │     └─> ToolRegistry.execute()
                    │           └─> SandboxManager.exec() (可选)
                    └─> PlanExecutorNode (复杂任务)
                          ├─> executor.get_next_layer() (拓扑排序)
                          ├─> spawn_agent() (并行)
                          └─> ReplannerNode (层间重规划)
```

### 1.3 目录结构一览

```
qrclaw/
├── agent.py              # Agent 主入口，thread_local 状态管理
├── cli/                  # 命令行界面
│   ├── app.py            # 主程序入口，延迟导入模式
│   ├── display.py        # Rich 渲染
│   ├── input.py          # 多行输入（""" / --- 分隔符）
│   └── commands/         # /agent, /session, /skill 子命令
├── tools/                # 工具集（装饰器注册）
│   ├── registry.py       # @register 装饰器 + schema 生成
│   ├── filesystem.py     # read_file, write_file, list_directory
│   ├── shell.py          # run_shell (沙箱双模式)
│   ├── web.py            # web_search, web_fetch
│   ├── memory_tools.py   # 简单记忆 CRUD
│   ├── wiki_tools.py     # Wiki 智能拆分写入
│   ├── agent_tools.py    # create_agent, delete_agent
│   ├── spawn_agent.py    # 后台并行子 agent
│   ├── wait_agents.py    # thread.join 等待
│   └── skills.py         # use_skill 工具
├── memory/               # 记忆系统
│   ├── context/
│   │   ├── session.py    # 短期记忆，JSON 持久化
│   │   ├── context_manager.py  # 统一上下文，Dirty Flag 缓存
│   │   └── compress.py   # 递归摘要 + 滚动截断
│   ├── core/
│   │   └── long_term.py  # 中期记忆，YAML+MD frontmatter
│   └── wiki/
│       └── wiki_memory.py # 长期记忆，LLM 智能拆分
├── graph/                # 任务执行图
│   ├── runner.py         # GraphRunner 入口
│   ├── executor.py       # get_next_layer() 拓扑排序
│   ├── router.py         # RouterNode (复杂度路由)
│   ├── react_loop.py     # ReactLoopNode (ReAct 循环)
│   ├── plan_executor.py  # PlanExecutorNode (DAG 执行)
│   ├── replanner.py      # ReplannerNode (重规划)
│   └── memory_extraction.py  # 后台记忆提取
├── sandbox/              # Docker 沙箱
│   ├── config.py         # Pydantic 模型 + ConfigManager 单例
│   ├── manager.py        # SandboxManager 单例，容器生命周期
│   ├── container.py      # ContainerManager，Docker CLI 封装
│   └── validator.py      # 挂载路径安全验证
├── providers/            # LLM 抽象层
│   ├── base.py           # LLMProvider ABC + ToolCall/LLMResponse
│   └── litellm_provider.py  # LiteLLM 实现，自动推断提供商
├── skills/               # 技能系统
│   └── registry.py       # SkillRegistry 单例，SKILL.md 解析
├── web_search/           # 联网搜索
│   ├── provider_registry.py
│   ├── runtime.py        # 搜索运行时
│   └── types.py
├── logger/               # 日志模块 (Rich Handler)
├── config.py             # 全局配置常量 (os.environ 读取)
├── config_manager.py     # ~/.qrclaw/config.yaml 管理
├── workspace.py          # Workspace 类，路径派生
├── prompt.py             # System Prompt 分段构建
└── heartbeat.py          # 心跳维护任务调度
```

---

## 2. 核心设计模式总结

### 2.1 装饰器模式 — 工具注册

**文件**: `tools/registry.py`

```python
@register(description="读取本地文件内容", args_model=ReadFileArgs, confirm=False)
def read_file(...):
    ...
```

- **原理**: 导入 `from qrclaw.tools import filesystem` 触发 side-effect，`@register` 装饰器将函数元数据（名称、描述、Pydantic 参数模型）存入全局注册表 `_TOOLS`
- **优势**: 声明式工具定义，零配置自动发现；参数校验与业务逻辑分离
- **Schema 生成**: `_build_schema()` 从 Pydantic 模型自动生成 OpenAI Tool Schema，兼容 `$ref` 递归展开（解决 Gemini 不支持 `$ref` 的问题）

### 2.2 单例模式 — 全局唯一实例

**应用场景**:
| 类 | 文件 | 用途 |
|---|---|---|
| `ConfigManager` | `sandbox/config.py` | 沙箱配置（`~/.qrclaw/permissions.yaml`） |
| `SandboxManager` | `sandbox/manager.py` | 容器生命周期管理 |
| `SkillRegistry` | `skills/registry.py` | 技能目录只扫描一次 |

**实现方式**: `__new__` 方法 + 类变量检查

```python
class ConfigManager:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance
```

**SkillRegistry 特殊设计**: `_loaded` + `_loaded_dir` 双重检查，目录变化时自动重新加载

### 2.3 ABC 抽象模式 — LLM 提供商解耦

**文件**: `providers/base.py`

```python
class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] = None, ...) -> LLMResponse:
        pass
```

- **好处**: 业务层（graph/executor）不绑定具体 LLM 实现，可热插拔切换提供商
- **当前实现**: `LiteLLMProvider`，通过 litellm 库统一调用 100+ 模型
- **扩展路径**: 新增 Provider 只需在 `providers/__init__.py` 的 `_REGISTRY` 注册 + 实现 `chat()` 接口

### 2.4 拓扑排序 — DAG 分层并行

**文件**: `graph/executor.py`

```python
def get_next_layer(steps: list[PlanStep]) -> list[PlanStep]:
    """返回所有 in_degree=0 的步骤，同一层可并行执行"""
    in_degree = {s.id: len(s.depends_on) for s in steps}
    # Kahn's algorithm 变体，取当前可执行层
```

- **应用**: `PlanExecutorNode` 按拓扑序取 `in_degree=0` 的步骤层
- **层内并行**: `threading.Thread` 批量启动同层子 agent
- **层间串行**: 等待一层全部完成后，调用 `ReplannerNode` 重规划，再取下一层

### 2.5 线程隔离 — thread_local 状态

**文件**: `agent.py`

```python
_thread_local = threading.local()

def run(user_input, session, console, workspace, ...):
    _thread_local.session = session
    _thread_local.workspace = workspace
    _thread_local.agent_depth = agent_depth
    ...

def get_session():
    return _thread_local.session
```

- **ContextManager 额外隔离**: `_ctx_thread_local.ctx` 保存/恢复栈，实现嵌套 agent 的状态隔离
- **子 agent 恢复**: `run_sub_agent()` 在 finally 块恢复父 agent 状态，防止状态泄露

### 2.6 兼容层模式 — 渐进式重构

**文件**: `memory/session.py`, `memory/step_result.py`, `memory/long_term.py`

```python
# memory/session.py 是兼容层，实际实现在 memory/context/session.py
from qrclaw.memory.context.session import Session, list_sessions, count_tokens
```

- **原理**: 根目录文件作为 re-export 层，将分散的实现重新导出为统一 API
- **好处**: 支持从旧路径迁移到新路径，平滑过渡，不破坏已有调用

### 2.7 缓存失效模式 — Dirty Flag

**文件**: `memory/context/context_manager.py`

```python
if self._system_prompt_cache is None or self._dirty:
    self._system_prompt_cache = build_system_prompt(...)
```

- **原理**: `write_memory` / `write_wiki_page` 调用 `_invalidate_context_manager_cache()` 置 `_dirty=True`，下次 `get_messages()` 时重建缓存
- **优势**: 避免每次调用都重建 System Prompt，只在记忆变更时才重建

### 2.8 单例+工厂混合 — Workspace

**文件**: `workspace.py`

```python
class Workspace:
    def __init__(self, agent_id: str = "default", _root: Path = None):
        self.root = _root or (AGENTS_ROOT / agent_id)
        ...

    def sub_agent(self, sub_id: str) -> "Workspace":
        """子 agent 共享父 agent 工作空间"""
        return Workspace(agent_id=f"{self.agent_id}-{sub_id}", _root=self.root)
```

- **子 agent 轻量化**: `sub_agent()` 复用父 `root`，只改变 `agent_id` 用于标识和日志区分
- **vs 持久化 agent**: `create_agent` 工具在 `~/.qrclaw/agents/` 下创建独立目录

---

## 3. 子系统亮点提炼

### 3.1 Agent 编排 — 分层路由

| 亮点 | 说明 |
|---|---|
| **RouterNode 智能分流** | LLM 一次调用判断任务复杂度，简单→ReactLoop，复杂→PlanExecutor |
| **ReactLoop 上限保护** | `MAX_ITERATIONS` 防止死循环 |
| **Replanner 层间重评估** | 每层 DAG 执行后重问目标是否达成，未达成返回新 PlanStep |
| **记忆提取后台化** | `MemoryExtractionNode` 阈值触发，后台线程写入 Wiki，不阻塞 CLI |
| **子 agent 并行+嵌套防护** | `spawn_agent` 批量并行，`is_sub_agent()` 防护禁止三级嵌套 |

### 3.2 工具注册 — 类型安全

| 亮点 | 说明 |
|---|---|
| **Pydantic 参数校验** | 所有工具参数通过 Pydantic 模型校验，类型错误自动报错 |
| **自动 Schema 生成** | 从 Pydantic 模型生成 OpenAI Tool Schema，无需手写 JSON |
| **`$ref` 递归展开** | 解决 Gemini 等不支持 `$ref` 的模型兼容问题 |
| **`confirm=True` 权限门** | 危险操作（write_file、delete_agent）执行前需用户确认 |
| **沙箱双模式** | `shell.py` 自动检测 `_is_sandbox_enabled()` 决定本地还是容器执行 |

### 3.3 记忆分层 — 职责清晰

| 层次 | 存储 | 触发 | 容量 |
|---|---|---|---|
| **短期 Session** | JSON 文件 | 每次对话 | 受上下文窗口限制 |
| **中期 LongTermMemory** | YAML+MD frontmatter | 手动 write_memory | 200 文件上限 |
| **长期 WikiMemory** | MD 文件 | 自动阈值触发 + LLM 拆分 | 按需扩展 |
| **上下文压缩** | 摘要+截断 | 60% 窗口阈值 | 20-25% 目标 |

**亮点**:
- 递归摘要：合并旧摘要一起压缩，防止信息逐次丢失
- 智能拆分：`write_wiki_page` 触发 LLM 分析，自动拆分成多个 Wiki 页面
- Frontmatter 元数据：记忆文件支持 YAML frontmatter，便于索引

### 3.4 沙箱隔离 — 纵深防御

| 层级 | 防护措施 |
|---|---|
| **容器隔离** | `python:3.11-slim` 镜像，`--read-only` 根文件系统，`cap-drop: ALL` |
| **资源限制** | 内存 512m，PIDs 上限 256 |
| **网络隔离** | 默认 `network: none`，可选 `bridge` |
| **挂载白名单** | 黑名单路径（`/etc`、`/proc`、`/sys`、`docker.sock`）双层检查 |
| **权限降级** | `no-new-privileges` 标志位 |
| **降级兜底** | 容器不可用时透明降级到 `subprocess.run()` |

### 3.5 LLM 抽象 — 统一多模型

| 亮点 | 说明 |
|---|---|
| **自动推断** | `_infer_provider()` 根据 `base_url` 自动拼接 provider/model 前缀 |
| **内置 11 个映射** | OpenAI/Anthropic/DeepSeek/Vertex/Azure/Cohere/Mistral/HuggingFace/MiniMax/OpenRouter/Google |
| **消息清洗** | 过滤 `refusal/annotations/audio/function_call` 冗余字段 |
| **指数退避重试** | 对 `RateLimitError/ServiceUnavailableError` 执行最多 3 次重试 |
| **统一响应契约** | `ToolCall` dataclass 规范化工具调用格式 |

---

## 4. 技术选型理由

### 4.1 为什么用 litellm？

| 考量 | 答案 |
|---|---|
| **多模型统一** | 一行代码切换 OpenAI/Claude/Gemini/DeepSeek，无需写多个适配器 |
| **Token 计算** | 内置 `token_counter` 比 tiktoken 更准确（支持 MiniMax 等非 OpenAI 模型） |
| **Provider 自动映射** | 根据 base_url 自动识别 100+ 模型前缀，降低配置成本 |
| **重试机制** | 内置对特定错误的指数退避，无需手写装饰器 |
| **成本** | 避免为每个模型单独维护 SDK，减少依赖体积 |

### 4.2 为什么用 threading.local？

| 考量 | 答案 |
|---|---|
| **轻量级隔离** | 不需要进程/容器级别的开销，thread-local 足够 |
| **子 agent 并行** | `threading.Thread` 启动子 agent，每个线程独立状态 |
| **零冲突** | 每个线程的 session/workspace/context 互不干扰 |
| **无侵入** | 调用方无需传递 context 参数，`get_session()` 全局获取 |
| **vs 上下文参数传递** | 避免每个函数签名都加 context 参数，减少样板代码 |

### 4.3 为什么用 Pydantic v2？

| 考量 | 答案 |
|---|---|
| **类型安全** | 工具参数自动校验，类型错误提前暴露 |
| **Schema 生成** | `model_json_schema()` 直接生成 OpenAI Tool Schema |
| **验证器** | `field_validator` 可自定义复杂校验逻辑 |
| **序列化** | 工具调用结果可直接 JSON 序列化 |
| **生态** | litellm、instructor 等核心依赖都已支持 Pydantic v2 |

### 4.4 为什么用 Rich？

| 考量 | 答案 |
|---|---|
| **富文本渲染** | Panel、Syntax、Table 等组件让 CLI 输出美观易读 |
| **分页** | `Console.print` + `Pager` 自动处理长输出 |
| **颜色** | ANSI 颜色代码跨平台兼容 |
| **vs 标准 print** | 不需要手写 ANSI 转义序列 |
| **vs curses** | API 简单，足够满足需求 |

### 4.5 为什么用 YAML 配置？

| 考量 | 答案 |
|---|---|
| **可读性** | YAML 比 JSON 更适合人类编辑，支持注释 |
| **分层结构** | 嵌套配置（llm/model、llm/base_url）自然表达 |
| **深度合并** | `_deep_merge()` 支持局部覆盖默认配置 |
| **vs TOML** | TOML 不支持注释（`pyproject.toml` 除外） |
| **vs INI** | INI 不支持嵌套结构 |

---

## 5. 扩展性设计分析

### 5.1 工具扩展 — 装饰器即插件

```python
# 新增一个工具，无需修改任何注册表代码
from qrclaw.tools.registry import register
from pydantic import BaseModel

class MyArgs(BaseModel):
    param: str

@register(description="我的新工具", args_model=MyArgs)
def my_tool(param: str):
    ...
```

**扩展流程**:
1. 在 `tools/` 下新建文件或加入现有文件
2. 使用 `@register` 装饰器声明
3. 导入 `qrclaw.tools`（`app.py` 已自动完成），side-effect 自动注册
4. Schema 自动生成，Tool Call 自动路由

### 5.2 LLM Provider 扩展

```python
# 新增 provider 只需:
# 1. 实现 LLMProvider ABC
class MyProvider(LLMProvider):
    def chat(self, messages, tools=None, ...):
        ...

# 2. 在 providers/__init__.py 注册
_REGISTRY = {"litellm": ..., "my": "path.to.MyProvider"}
```

### 5.3 Skill 扩展 — SKILL.md 即插件

```
skills/
└── my_skill/
    └── SKILL.md   # 包含 name/description/args/完整内容
```

- 系统自动扫描 `skills/` 目录
- 解析 YAML frontmatter 获取元数据
- 轻量级描述注入 System Prompt，完整内容按需加载
- **即插即用，无需代码修改**

### 5.4 子 Agent 扩展

```python
# 工具中调用
from qrclaw.agent import run_sub_agent

result, sub_session = run_sub_agent(
    task="分析项目结构",
    workspace=workspace.sub_agent("analyzer"),
    agent_id="analyzer"
)
```

- **批量并行**: `spawn_agent` 启动多个子 agent，`wait_agents` 统一等待
- **嵌套防护**: `is_sub_agent()` 禁止超过两层嵌套
- **状态隔离**: thread_local + ContextManager 保存/恢复栈

### 5.5 搜索 Provider 扩展

```
web_search/
├── provider_registry.py  # 注册表
├── providers/
│   ├── tavily.py
│   ├── duckduckgo.py     # 新增只需在这里实现
│   └── google.py
├── runtime.py            # 统一调度
└── types.py
```

---

## 6. 代码组织规范

### 6.1 `__init__.py` 导出控制

| 文件 | 导出策略 |
|---|---|
| `tools/__init__.py` | `from . import filesystem` 触发 side-effect（自动注册） |
| `providers/__init__.py` | 动态加载 Provider，单一导出 `provider: LLMProvider` |
| `memory/__init__.py` | re-export 兼容层 + 导出 `MemoryManager` |

### 6.2 延迟导入模式

**文件**: `cli/app.py`

```python
# 错误顺序:
# import qrclaw.tools  ← get_logger() 在 setup_logger 前执行

# 正确顺序:
setup_logger(...)      # 先初始化日志系统
import qrclaw.tools    # 再触发工具注册（get_logger 正常写入日志文件）
from qrclaw.agent import run
```

- **问题**: 各模块顶层 `get_logger()` 会在日志系统初始化前执行
- **解决**: `app.py` 中先 `setup_logger`，再延迟导入子模块

### 6.3 Thread-Local 状态隔离规范

```python
# agent.py 中的状态隔离模式
_thread_local = threading.local()

def run_sub_agent(...):
    old_depth = get_agent_depth()
    try:
        set_agent_depth(old_depth + 1)      # 递增
        save_context_manager_state()          # 保存
        result = run(...)                     # 执行
        restore_context_manager_state()       # 恢复
    finally:
        set_agent_depth(old_depth)           # 一定恢复
        restore_context_manager_state()      # 即使异常也恢复
```

- **原则**: 状态隔离 + 嵌套深度 + ContextManager 保存/恢复
- **finally 块**: 确保异常时也能恢复，防止状态泄露

### 6.4 分段 Prompt 构建

**文件**: `prompt.py`

```python
def build_system_prompt(
    heartbeat_file: Path | None = None,
    is_sub_agent: bool = False,
    agent_file: Path | None = None,
    skills_dir: Path | None = None,
    memory_dir: Path | None = None,
) -> str:
    sections = [
        _build_identity_section(agent_file),   # AGENT.md
        _build_tooling_section(tool_names),     # 工具列表
        _build_skills_section(skill_registry),   # 技能
        _build_behavior_section(is_sub_agent), # 行为准则
        _build_safety_section(),                 # 安全边界
        _build_workspace_section(),              # 工作环境
        _build_memory_section(memory_dir),      # Wiki 记忆索引
        _build_heartbeat_section(heartbeat_file), # 心跳任务
    ]
```

- **模块化**: 每个 section 独立构建，便于单独测试和修改
- **按需注入**: 全部参数可选，不影响默认行为
- **vs 单一模板**: 不需要维护一个巨大的 prompt 字符串

---

## 7. 架构亮点总览

| # | 亮点 | 类型 | 说明 |
|---|---|---|---|
| 1 | **分层任务执行图** | 架构 | Router→ReactLoop/PlanExecutor→Replanner，支持简单任务直接执行、复杂任务 DAG 并行 |
| 2 | **装饰器工具注册** | 设计模式 | `@register` 装饰器实现零配置工具发现 + Pydantic 参数校验 |
| 3 | **三级记忆分层** | 架构 | Session（短期）→LongTermMemory（中期）→WikiMemory（长期），按需压缩 |
| 4 | **Dirty Flag 缓存** | 性能 | System Prompt 只在记忆变更时重建，避免重复构建 |
| 5 | **Docker 沙箱硬隔离** | 安全 | 容器级隔离 + 内存/PIDs 限制 + 网络隔离 + 敏感路径黑名单 |
| 6 | **LiteLLM 统一抽象** | 可扩展性 | 一套接口调用 100+ 模型，自动推断 Provider 前缀 |
| 7 | **Skill 按需加载** | 性能 | 轻量级描述注入 System Prompt，完整内容用时加载，节省 Token |
| 8 | **拓扑分层并行** | 架构 | DAG 步骤按 `in_degree=0` 分层，层内并行、层间串行 |
| 9 | **Thread-Local 隔离** | 架构 | 子 agent 并行执行时，每个线程独立 session/workspace/context |
| 10 | **兼容层渐进重构** | 工程化 | `memory/session.py` 等兼容层支持从旧路径平滑迁移到新路径 |
| 11 | **延迟导入日志保护** | 工程化 | `app.py` 先 `setup_logger` 再导入子模块，避免日志写入错误文件 |
| 12 | **Wiki 智能拆分** | 智能 | LLM 分析记忆内容，自动拆分成多个相关 Wiki 页面 |
| 13 | **递归摘要压缩** | 智能 | 合并旧摘要一起压缩，防止信息逐次丢失 |
| 14 | **子 agent 嵌套防护** | 安全 | `is_sub_agent()` 禁止超过两层嵌套，防止状态污染 |
| 15 | **双模沙箱执行** | 可扩展性 | `shell.py` 自动检测沙箱开关，本地/容器执行对调用方透明 |
| 16 | **Pydantic Schema 自动生成** | 开发效率 | 工具参数模型自动生成 OpenAI Tool Schema，无需手写 JSON |
| 17 | **ConfigManager 单例** | 设计模式 | 沙箱配置只加载一次，thread-safe |
| 18 | **指数退避重试** | 容错 | LLM 调用对 RateLimit/ServiceUnavailable 错误自动重试 3 次 |
| 19 | **路径黑名单双层验证** | 安全 | `sandbox/config.py` + `sandbox/container.py` 双重检查挂载路径 |
| 20 | **分段 System Prompt** | 可维护性 | 每个 section 独立构建，便于单独测试、修改和扩展 |

---

## 8. 与 Claude Code 的设计借鉴

根据源码注释，QRClaw 借鉴了 Claude Code 的多项设计：

| 借鉴项 | Claude Code 实现 | QRClaw 实现 |
|---|---|---|
| 记忆目录结构 | `~/.claude/memory/` | `~/.qrclaw/agents/{id}/memory/` |
| 记忆文件限制 | MAX_MEMORY_FILES=200 等 | `memory/core/long_term.py` 中的常量 |
| Session 恢复 | resume 模式 | `Session(resume=True)` |
| 技能系统 | SKILL.md 格式 | `skills/registry.py` 完全兼容 |
| 压缩策略 | 递归摘要 | `memory/context/compress.py` |

---

## 9. 总结

QRClaw 是一个**工程化程度极高**的自主 Agent 框架，核心亮点在于：

1. **分层执行图**：Router + ReactLoop + PlanExecutor + Replanner 的组合，优雅解决了简单任务与复杂任务的不同处理需求
2. **类型安全工具系统**：装饰器注册 + Pydantic 校验 + 自动 Schema 生成，实现了声明式、类型安全、可扩展的工具定义
3. **三级记忆分层**：Session → LongTermMemory → WikiMemory，配合压缩和智能拆分，在上下文窗口限制下最大化信息保留
4. **纵深沙箱安全**：Docker 容器隔离 + 资源限制 + 网络隔离 + 路径黑名单 + 降级兜底，提供多层防护
5. **极致的 Token 优化**：Skill 按需加载、Wiki 索引注入、递归摘要压缩、Dirt Flag 缓存，处处考虑上下文窗口效率

架构上没有明显的设计缺陷。可能的改进方向包括：
- **测试覆盖**：当前未见 `tests/` 目录，核心模块应有单元测试
- **异步支持**：当前使用 `threading.Thread`，未来可考虑 `asyncio` 进一步提升并发效率
- **插件市场**：Skill 缺乏分享机制，可建立 SKILL.md 仓库生态
