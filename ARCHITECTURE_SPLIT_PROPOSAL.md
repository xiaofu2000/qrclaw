# QRClaw 架构拆分讨论报告

**编制日期**: 2026-04-12  
**依据**: Step 1-5 战报综合分析  
**目标**: 为 memory_extraction.py 和 memory/ 模块的架构拆分提供决策依据

---

## 一、当前架构问题汇总

### 1.1 代码量分布数据

| 模块/文件 | 行数 | 定位 | 问题评级 |
|-----------|------|------|----------|
| `memory_extraction.py` | 724 行 | Graph 节点 | 🔴 严重过载 |
| `memory_manager.py` | 433 行 | 记忆核心 | 🟡 需审查 |
| `memory/` 目录总计 | ~2824 行 | 记忆系统 | 🟡 结构混乱 |
| `react_loop.py` | 242 行 | Graph 节点 | ✅ 正常 |
| `plan_executor.py` | 195 行 | Graph 节点 | ✅ 正常 |
| `router.py` | 120 行 | Graph 节点 | ✅ 正常 |
| `replanner.py` | 161 行 | Graph 节点 | ✅ 正常 |

**核心问题**: `memory_extraction.py` 代码量是其他节点的 **3-6 倍**，是单一最大文件。

### 1.2 MemoryExtractionNode 职责清单（共 12 项）

| # | 职责域 | 代码行 | 问题描述 |
|---|--------|--------|----------|
| A | 配置管理 | ~15 行 | `ExtractionConfig` 可独立 |
| B | 触发判断 | ~40 行 | Token + 工具调用双阈值，逻辑可复用 |
| C | 消息截断 | ~25 行 | uuid 定位逻辑，纯函数可抽取 |
| D | 消息过滤 | ~30 行 | 过滤 tool 返回/tool_calls，纯函数 |
| E | Prompt 构建 | ~40 行 | 模板注入逻辑，与 LLM 调用耦合 |
| F | **LLM 调用** | ~35 行 | **与业务逻辑强耦合** 🔴 |
| G | 记忆存取 | ~5 行 | WikiMemory 接口调用 |
| H | 页面整理 | ~40 行 | 独立 LLM 整理逻辑 |
| I | 冷却追踪 | ~5 行 | 简单字典追踪 |
| J | Token 估算 | ~25 行 | 纯计算逻辑，可独立 |
| K | **线程管理** | ~50 行 | **与 Node 生命周期绑定** 🟡 |
| L | 状态查询 | ~10 行 | 统计接口，简单 |

### 1.3 记忆系统两套并存问题

| 维度 | WikiMemory | MemoryManager |
|------|------------|---------------|
| 存储结构 | 扁平 `pages/` | 分层 `user/feedback/project/` |
| 入口文件 | `index.json` + `index.md` | `MEMORY.md` |
| LLM 集成 | ✅ 有（load_index） | ❌ 无 |
| 模糊匹配 | ✅ fuzzy_find_name | ❌ 无 |
| 操作日志 | ✅ log.md | ❌ 无 |
| 依赖关系 | 强依赖 IndexManager | 独立运作 |

**结论**: 两套系统职责重叠但设计目标不同，建议明确分工或统一。

---

## 二、拆分方案推荐

### 2.1 拆分后目标架构

```
graph/nodes/memory_extraction.py (拆分后)
├── memory_extraction_node.py    # 精简节点，只负责编排 (~150行)
├── extraction_policy.py         # 阈值策略独立 (~50行)
├── extraction_analyzer.py       # LLM 调用封装 (~80行)
├── token_estimator.py           # Token 估算工具函数 (~30行)
└── background_extractor.py       # 后台线程管理 (~60行)

memory/extraction/ (新目录)
├── __init__.py
├── schemas.py                   # Pydantic Schema 定义
├── prompts.py                   # Prompt 模板
├── consolidator.py              # 页面整理逻辑
└── config.py                    # 配置常量
```

### 2.2 新模块详细设计

#### 2.2.1 ExtractionAnalyzer（优先级 A）

**职责**: 封装 LLM 调用逻辑，与业务解耦

```python
# extraction_analyzer.py
from typing import Protocol
from qrclaw.graph.nodes.memory_extraction import ExtractionSchema, ConsolidatePageSchema

class ExtractionAnalyzer(Protocol):
    """记忆提取分析器接口"""
    
    def analyze(self, prompt: str) -> Optional[ExtractionSchema]:
        """分析对话内容，提取记忆"""
        ...
    
    def consolidate(self, name: str, content: str) -> Optional[ConsolidatePageSchema]:
        """整理页面内容，去重合并"""
        ...

class LiteLLMExtractionAnalyzer:
    """基于 LiteLLM 的实现"""
    
    def __init__(self, provider: LiteLLMProvider, temperature: float = 0.1):
        self.provider = provider
        self.temperature = temperature
    
    def analyze(self, prompt: str) -> Optional[ExtractionSchema]:
        # instructor 调用逻辑
        ...
    
    def consolidate(self, name: str, content: str) -> Optional[ConsolidatePageSchema]:
        # 页面整理逻辑
        ...
```

#### 2.2.2 ExtractionPolicy（优先级 A）

**职责**: 独立阈值检查逻辑，可复用

```python
# extraction_policy.py
from dataclasses import dataclass

@dataclass
class ExtractionPolicy:
    """提取策略配置"""
    minimum_message_tokens_to_init: int = 10000
    minimum_tokens_between_update: int = 5000
    tool_calls_between_updates: int = 3
    
    def should_extract(
        self,
        token_count: int,
        tokens_at_last: int,
        tool_calls_since: int,
        is_initialized: bool,
    ) -> tuple[bool, str]:
        """检查是否应该触发提取，返回 (结果, 原因)"""
        ...
    
    def count_tool_calls(self, messages: list, since_uuid: str = None) -> int:
        """计算指定消息后的工具调用次数"""
        ...
```

#### 2.2.3 BackgroundExtractor（优先级 B）

**职责**: 后台线程生命周期管理

```python
# background_extractor.py
class BackgroundExtractor:
    """后台提取器"""
    
    def __init__(
        self,
        extractor: 'MemoryExtractionNode',
        session_getter: Callable[[], Optional[Session]],
        interval_seconds: int = 60,
    ):
        self.extractor = extractor
        self.session_getter = session_getter
        self.interval = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
    
    def start(self) -> None:
        """启动后台线程"""
        ...
    
    def stop(self, timeout: float = 5) -> None:
        """停止后台线程"""
        ...
    
    def _run_loop(self) -> None:
        """后台循环逻辑"""
        ...
```

#### 2.2.4 TokenEstimator（优先级 B）

**职责**: Token 数量估算，纯函数无状态

```python
# token_estimator.py
def estimate_tokens(messages: list) -> int:
    """估算消息列表的 token 总数"""
    ...

def count_text_tokens(text: str) -> int:
    """估算文本的 token 数（中英文混合）"""
    return max(1, len(text) // 3)
```

#### 2.2.5 MemoryExtractionNode（精简后）

```python
# memory_extraction_node.py
class MemoryExtractionNode:
    """精简后的节点，只负责编排"""
    
    def __init__(
        self,
        memory: WikiMemory,
        policy: ExtractionPolicy = None,
        analyzer: ExtractionAnalyzer = None,
    ):
        self.memory = memory
        self.policy = policy or ExtractionPolicy()
        self.analyzer = analyzer or LiteLLMExtractionAnalyzer(provider)
        
        # 状态（精简后只保留核心状态）
        self._tokens_at_last_extraction: int = 0
        self._last_message_uuid: Optional[str] = None
        self._is_initialized: bool = False
        self._pending_extractions: list[ExtractionResult] = []
        
        self._background_extractor = BackgroundExtractor(
            extractor=self,
            session_getter=lambda: self._session_ref,
        )
    
    def check_and_extract(self, messages: list, token_count: int, ...) -> bool:
        # 1. 阈值检查（委托给 Policy）
        should, reason = self.policy.should_extract(...)
        if not should:
            return False
        
        # 2. 触发提取（委托给辅助方法）
        self._trigger_extraction(messages)
        return True
    
    def flush_pending(self) -> int:
        # 批量写入（LLM 分析委托给 Analyzer）
        for result in to_write:
            extraction = self.analyzer.analyze(result.prompt)
            ...
    
    def _consolidate_page(self, name: str) -> None:
        # 页面整理委托给 Analyzer
        self.analyzer.consolidate(name, page.content)
```

---

## 三、拆分优先级排序

### 3.1 优先级 A：必须拆分

| 模块 | 拆分原因 | 预期收益 |
|------|----------|----------|
| **ExtractionAnalyzer** | LLM 调用与业务逻辑强耦合，当前硬编码在 `_analyze_with_llm()` | 支持多 Provider 切换、便于单元测试、可复用 |
| **LLM 调用逻辑** | `_consolidate_page()` 重复 LLM 调用代码 | 消除重复、统一重试策略、统一错误处理 |

**拆分影响**: 
- 风险：低（接口简单，替换成本小）
- 测试：可独立 Mock provider 进行单元测试
- 推荐先拆分此模块

### 3.2 优先级 B：建议拆分

| 模块 | 拆分原因 | 预期收益 |
|------|----------|----------|
| **ExtractionPolicy** | 阈值检查逻辑可独立复用 | 便于调整参数、支持动态策略、可测试 |
| **BackgroundExtractor** | 线程管理与 Node 生命周期耦合 | 简化 Node 代码、支持独立的启动/停止 |
| **TokenEstimator** | 纯函数，无状态依赖 | 便于优化估算算法、可单独测试 |

**拆分影响**:
- 风险：中（涉及状态传递，需小心重构）
- 测试：Policy 和 TokenEstimator 可完全单元测试
- 推荐第二步拆分

### 3.3 优先级 C：可延后

| 模块 | 拆分原因 | 备注 |
|------|----------|------|
| **记忆系统统一** | WikiMemory vs MemoryManager | 需更高层决策，是否统一两套系统 |
| **Schema 独立文件** | Pydantic Schema 可单独模块 | 当前与 Node 同文件影响不大 |
| **Prompt 模板独立** | Prompt 模板可抽取 | 可延后考虑 |

---

## 四、拆分风险与注意事项

### 4.1 已知风险

| 风险 | 描述 | 缓解措施 |
|------|------|----------|
| **循环依赖** | 拆分后可能出现循环导入 | 使用 Protocol 接口解耦，延迟导入 |
| **状态泄漏** | 拆分后状态可能在多个模块中不同步 | 使用 dataclass/frozen 不可变状态，清晰边界 |
| **测试覆盖** | 重构可能引入回归 | 先写集成测试，逐步拆分 |

### 4.2 拆分注意事项

1. **保持接口稳定**: ExtractionAnalyzer 使用 Protocol 接口，便于后续替换实现
2. **渐进式重构**: 每次只拆分一个小模块，立即测试
3. **保留向后兼容**: 初期通过 `__getattr__` 转发，保持现有调用方式
4. **线程安全**: BackgroundExtractor 拆分时注意锁的传递

### 4.3 推荐拆分顺序

```
Step 1: ExtractionAnalyzer（最高优先级）
        ↓
Step 2: ExtractionPolicy + TokenEstimator（无依赖，可并行）
        ↓
Step 3: BackgroundExtractor
        ↓
Step 4: Schema + Prompt 模板独立文件
        ↓
Step 5: Memory 系统统一决策
```

---

## 五、附录

### 5.1 关键文件路径

| 文件 | 路径 | 备注 |
|------|------|------|
| memory_extraction.py | `qrclaw/graph/nodes/memory_extraction.py` | 待拆分主文件 |
| WikiMemory | `qrclaw/memory/wiki/wiki_memory.py` | 记忆存储层 |
| IndexManager | `qrclaw/memory/wiki/index.py` | 索引管理层 |
| memory_manager.py | `qrclaw/memory/core/memory_manager.py` | 需审查文件 |

### 5.2 关键配置常量

```python
MEMORY_INIT_THRESHOLD = 10000       # 初始化阈值
MEMORY_UPDATE_INTERVAL = 5000       # 更新间隔
MEMORY_TOOL_CALL_INTERVAL = 3        # 工具调用间隔
```

### 5.3 外部依赖注入点

```python
MemoryExtractionNode(
    memory: WikiMemory,              # 记忆存储
    provider: LiteLLMProvider,       # LLM 提供商（全局单例）
)
```
