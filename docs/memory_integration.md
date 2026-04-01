# MemoryExtractionNode 集成指南

## 概述

`MemoryExtractionNode` 实现了类似 Claude Code 的记忆提取机制：
- **默默执行**：不输出到 console，不影响用户感知
- **延迟更新**：收集多个候选记忆，评估后批量写入
- **冷却机制**：避免重复提取同一记忆

## 与 Graph 的集成方式

### 方式 1：在 GraphRunner 中集成（推荐）

```python
# qrclaw/graph/runner.py

class GraphRunner:
    def __init__(self):
        self.router = RouterNode()
        self.react_loop = ReactLoopNode()
        self.plan_executor = PlanExecutorNode()
        # 初始化记忆提取节点
        self.memory_extractor = MemoryExtractionNode(
            memory_manager=workspace.memory_manager
        )

    def run(self, ...):
        result = self.react_loop.run(...)
        
        # 在主循环结束后默默提取记忆
        self.memory_extractor.analyze_and_extract(
            session.messages, 
            current_round=iteration
        )
        
        # 检查是否需要批量写入
        if self.memory_extractor.should_flush():
            self.memory_extractor.flush_pending_extractions()
        
        return result
```

### 方式 2：后台线程模式

```python
# 启动后台记忆提取
integration = MemoryExtractionIntegration(extractor)
integration.start_background_extraction(
    session,
    trigger_rounds=[5, 10, 20, 50],  # 每5轮检查一次
    interval_seconds=30,
)

# 在 GraphRunner 结束时停止
integration.stop_background_extraction()
```

### 方式 3：在 ReactLoopNode 内部集成

```python
# 在 qrclaw/graph/nodes/react_loop.py 的循环结束后添加

class ReactLoopNode:
    def run(self, session, ...):
        iteration = 0
        for iteration in range(MAX_ITERATIONS):
            # ... 现有逻辑 ...
            
            # 循环结束时调用
            if hasattr(self, 'memory_extractor'):
                self.memory_extractor.analyze_and_extract(
                    session.messages,
                    current_round=iteration
                )
        
        return response.content
```

## 提取规则

内置规则覆盖以下场景：

| 类型 | 关键词 | 记忆名称 | 冷却轮数 |
|------|--------|----------|----------|
| USER | 偏好、喜欢、禁止 | 用户偏好 | 5 |
| USER | 工作目录、只...目录下 | 工作规则 | 20 |
| USER | 主人、称呼 | 用户角色 | 50 |
| FEEDBACK | 不要我、你不要 | 行为指导 | 10 |
| FEEDBACK | 按照、遵循、参考 | 执行规范 | 15 |
| PROJECT | 项目、仓库 | 项目配置 | 20 |
| PROJECT | git、github、分支 | 版本控制规则 | 30 |
| REFERENCE | 文档、README、wiki | 文档参考 | 50 |

## 自定义规则

```python
from qrclaw.memory.types import MemoryType
from qrclaw.graph.nodes.memory_extraction import ExtractionRule, MemoryExtractionNode

# 添加自定义规则
custom_rules = [
    ExtractionRule(
        pattern=r"使用.*框架|基于.*技术",
        memory_type=MemoryType.PROJECT,
        name_template="技术栈",
        priority=8,
        cooldown_rounds=30,
    ),
]

extractor = MemoryExtractionNode(
    memory_manager=workspace.memory_manager,
    rules=custom_rules,  # 覆盖默认规则
    cooldown_enabled=True,
    batch_size=3,
)
```

## 延迟写入机制

```
┌─────────────────────────────────────────────────────┐
│  ReAct 循环                                          │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                    │
│  │轮次1│→│轮次2│→│轮次3│→│轮次4│→ ...               │
│  └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘                    │
│     ↓        ↓        ↓        ↓                      │
│  ┌─────────────────────────────────┐                  │
│  │  MemoryExtractionNode.analyze  │  默默分析         │
│  └────────────┬────────────────────┘                  │
│               ↓                                       │
│  ┌─────────────────────────────────┐                  │
│  │  _pending_extractions 队列      │  收集候选         │
│  └────────────┬────────────────────┘                  │
│               ↓                                       │
│  ┌─────────────────────────────────┐                  │
│  │  batch_size = 5 时触发写入      │  批量保存         │
│  └─────────────────────────────────┘                  │
│               ↓                                       │
│  ┌─────────────────────────────────┐                  │
│  │  MemoryManager.save_entry()     │  写入 MEMORY.md  │
│  └─────────────────────────────────┘                  │
└─────────────────────────────────────────────────────┘
```

## 冷却机制

同一记忆被提取后，会进入冷却期：

```python
# 示例
rule = ExtractionRule(
    pattern="偏好",
    memory_type=MemoryType.USER,
    name_template="用户偏好",
    cooldown_rounds=5,  # 5轮内不重复提取
)

# 第 3 轮提取了"用户偏好"
_cooldown_tracker["用户偏好"] = 3

# 第 7 轮再次遇到"用户偏好"
# 7 - 3 = 4 < 5 → 跳过（还在冷却中）
# 第 9 轮
# 9 - 3 = 6 >= 5 → 可以再次提取
```

## Graph 条件边集成

```
GraphRunner
    │
    ├── Router ──→ ReactLoop ──→ MemoryExtraction (默默)
    │                            │
    │                            └── 触发条件边：
    │                                - iteration_end
    │                                - heartbeat
    │                                - session_end
    │
    └── PlanExecutor
            │
            └── ReactLoop ──→ MemoryExtraction (默默)
```

## 调试

```python
# 启用调试日志
import logging
logging.getLogger("qrclaw.graph.nodes.memory_extraction").setLevel(logging.DEBUG)

# 检查待写入队列
print(f"待写入数量: {extractor.get_pending_count()}")

# 强制写入
extractor.flush_pending_extractions()
```
