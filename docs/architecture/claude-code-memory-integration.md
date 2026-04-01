# Claude Code 记忆系统整合架构

> **目标**：将 Claude Code 的结构化记忆系统整合进 QRClaw，增强 Agent 的持久化学习和上下文管理能力。

---

## 1. 设计目标

1. **结构化记忆存储**：支持 Topic 文件分离 + MEMORY.md 入口索引 ✅ 已实现
2. **多类型记忆分类**：支持 user / feedback / project / reference 四种类型 ✅ 已实现
3. **自动记忆提取**：定期触发 `extractMemories`，基于上下文智能生成记忆（Phase 2）
4. **会话级摘要**：会话结束时自动生成 SessionMemory 结构化笔记（Phase 3）
5. **上下文感知加载**：根据任务类型动态加载相关记忆子集（Phase 2）

---

## 2. 已实现模块

### 2.1 记忆类型定义 ✅

```python
# qrclaw/memory/types.py

class MemoryType(Enum):
    USER = "user"           # 用户角色、知识、偏好
    FEEDBACK = "feedback"   # 行为指导、错误纠正
    PROJECT = "project"      # 项目上下文、技术债务
    REFERENCE = "reference" # 外部系统指针、文档链接

@dataclass
class MemoryFile:
    name: str
    description: str
    type: MemoryType
    content: str
    created_at: datetime
    updated_at: datetime
    
    def to_frontmatter(self) -> str: ...
    @classmethod
    def from_frontmatter(cls, filepath: str, text: str) -> Optional["MemoryFile"]: ...
```

### 2.2 MemoryManager ✅

```python
# qrclaw/memory/memory_manager.py

class MemoryManager:
    """增强版长期记忆管理器"""
    
    def __init__(self, memory_dir: Path): ...
    
    # 核心 CRUD
    def save_memory(name, description, content, memory_type, update_existing=True) -> bool: ...
    def get_memory(name, memory_type=None) -> Optional[MemoryFile]: ...
    def delete_memory(name, memory_type=None) -> bool: ...
    
    # 批量操作
    def scan_all_memories() -> list[MemoryFile]: ...
    def get_memories_by_type(memory_type: MemoryType) -> list[MemoryFile]: ...
    def search_memories(query: str) -> list[MemoryFile]: ...
    
    # 索引管理
    def update_entrypoint(memory: MemoryFile) -> bool: ...
    def rebuild_entrypoint() -> bool: ...
    
    # 兼容性
    def load() -> str: ...  # 兼容 LongTermMemory
    def append(content, title) -> bool: ...  # 兼容 LongTermMemory
```

### 2.3 目录结构 ✅

```
~/.qrclaw/agents/{agent_id}/
├── memory/                          # 持久化记忆（增强版）
│   ├── MEMORY.md                    # 入口索引
│   ├── user/
│   │   └── <name>.md               # 用户记忆
│   ├── feedback/
│   │   └── <name>.md              # 反馈记忆
│   ├── project/
│   │   └── <name>.md              # 项目记忆
│   └── reference/
│       └── <name>.md              # 外部引用
├── MEMORY.md                        # 兼容旧接口
├── sessions/
└── ...
```

### 2.4 MEMORY.md 索引格式 ✅

```markdown
# QRClaw 记忆索引

- [用户角色](用户角色.md) — 数据科学家，专注日志
- [代码风格反馈](代码风格反馈.md) — 不要用 mock
```

### 2.5 Frontmatter 元数据 ✅

```yaml
---
name: 用户角色
description: 数据科学家，专注日志分析
type: user
created_at: 2026-04-01T23:24:54.672521
updated_at: 2026-04-01T23:24:54.672529
---

# 用户角色

我是赵岩，数据科学家，专注日志系统。
```

---

## 3. 工具函数 ✅

### 3.1 已实现工具

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `write_memory` | 写入记忆到指定类型 | content, title, memory_type |
| `read_memory` | 读取 MEMORY.md 索引 | - |
| `search_memory` | 搜索记忆 | query |
| `review_memory` | 审查整理记忆 | action, keep_recent, memory_type |

### 3.2 工具调用示例

```
# 写入用户角色记忆
write_memory(
    content="用户是数据科学家，专注日志系统",
    title="用户角色",
    memory_type="user"
)

# 写入反馈记忆
write_memory(
    content="不要用 mock 测试，要用真实数据库",
    title="测试偏好",
    memory_type="feedback"
)

# 搜索记忆
search_memory("科学家")

# 审查记忆
review_memory(action="analyze")
review_memory(action="cleanup")
review_memory(action="rebuild")
```

---

## 4. 文件清单

```
qrclaw/
├── memory/
│   ├── __init__.py              # [MODIFY] 导出 MemoryManager, MemoryFile, MemoryType
│   ├── types.py                 # [NEW] MemoryType, MemoryFile, MemoryIndex
│   ├── memory_manager.py        # [NEW] MemoryManager 核心类
│   ├── long_term.py             # [KEEP] 兼容旧接口
│   ├── session.py               # [KEEP] 会话管理
│   ├── context_manager.py       # [MODIFY] 集成 MemoryManager
│   ├── step_result.py           # [KEEP] 步骤结果
│   │
│   └── tools/
│       ├── memory_tools.py      # [MODIFY] write/read/search_memory
│       └── review_memory.py     # [MODIFY] 增强版 review_memory
│
├── workspace.py                 # [MODIFY] 添加 get_memory_manager()
│
└── docs/
    └── architecture/
        └── claude-code-memory-integration.md  # 本文档
```

---

## 5. 实现计划

### Phase 1: 基础存储 ✅ 完成

- [x] 定义 `MemoryType` 枚举和 `MemoryFile` 数据结构
- [x] 实现 `MemoryManager` 核心类
- [x] 实现 `MEMORY.md` 入口索引管理
- [x] 实现 frontmatter 解析和生成
- [x] 保留 `LongTermMemory` 兼容接口

### Phase 2: 上下文集成（待开发）

- [ ] 实现 `ContextIntegrator.build_context()`
- [ ] 修改 `prompt.py` 注入记忆内容
- [ ] 添加记忆可见性控制（私有/团队）
- [ ] 实现 `MemoryExtractor.extract_memories()`

### Phase 3: 智能提取（待开发）

- [ ] 设计提取 Prompt 模板
- [ ] 实现 `ExtractTrigger` 节流机制
- [ ] 集成到心跳任务
- [ ] 实现 `SessionSummarizer.summarize_session()`

---

## 6. 参考实现

本设计参考以下 Claude Code 源文件：

| 原文件 | 功能 | QRClaw 映射 |
|--------|------|-------------|
| `memdir.ts` | 记忆目录管理 | `MemoryManager` |
| `memoryTypes.ts` | 类型定义 | `types.py` |
| `memoryScan.ts` | 文件扫描 | `MemoryManager.scan_all_memories()` |
| `memoryFs.ts` | 文件系统操作 | `MemoryFile.to_frontmatter()` |
| `extractMemories.ts` | 记忆提取 | `MemoryExtractor` (Phase 2) |
| `sessionMemory.ts` | 会话摘要 | `SessionSummarizer` (Phase 3) |

---

## 7. 测试验证

```python
# 测试 MemoryManager
from qrclaw.memory import MemoryManager, MemoryType

mm = MemoryManager(Path("~/.qrclaw/memory"))

# 保存记忆
mm.save_memory(
    name="用户角色",
    description="数据科学家",
    content="# 用户角色\n\n我是赵岩",
    memory_type=MemoryType.USER
)

# 搜索记忆
results = mm.search_memories("科学家")

# 重建索引
mm.rebuild_entrypoint()
```

---

*文档版本：1.1.0 | 更新日期：2026-04-01*
