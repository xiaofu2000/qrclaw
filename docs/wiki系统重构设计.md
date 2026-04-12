# Wiki 系统重构设计方案

## 任务交付报告

- **状态**：成功
- **动作简述**：生成 wiki 系统重构设计方案

---

## 做了什么

分析了现有系统架构（wiki/、core/、memory/）的代码，设计了新的 wiki 系统架构，输出到：

`/Users/fuqingrong/Documents/agent开发/qrclaw/docs/wiki系统重构设计.md`

---

## 核心情报栈 (Payload)

### 一、新目录结构

```
qrclaw/memory/
├── __init__.py          # 统一导出入口（修改）
├── wiki/                # 新核心模块
│   ├── __init__.py
│   ├── page.py          # [保留] WikiPage 数据类
│   ├── index.py         # [保留] IndexManager 索引管理
│   ├── wiki_memory.py   # [保留] WikiMemory CRUD
│   └── manager.py       # [新增] WikiMemoryManager（替代 MemoryManager）
├── session.py           # [保留] Session 会话级记忆
├── token_utils.py       # [保留] Token 计算工具
├── long_term.py         # [修改] 兼容层，内部重导出 WikiMemoryManager
└── .DS_Store            # 忽略
```

**删除目录**：`core/`、`storage/`、`types/`、`entrypoint/`、`factory/`、`context/`、`compression/`

### 二、各模块职责

| 模块 | 职责 |
|------|------|
| `WikiPage` | 数据类：frontmatter 序列化/反序列化，对应 pages/<name>.md |
| `IndexManager` | 索引管理：维护 index.json + index.md + log.md 同步 |
| `WikiMemory` | CRUD 入口：save_page/get_page/search_page/delete_page |
| `WikiMemoryManager` | **新增**，整合 WikiMemory + 兼容接口，暴露 `load_index()` 给 workspace |
| `memory/__init__.py` | 统一导出 WikiMemoryManager 作为默认 MemoryManager |
| `long_term.py` | **修改**为兼容层：`WikiMemoryManager.for_workspace()` |

### 三、新增/修改文件清单

| 操作 | 文件路径 | 说明 |
|------|---------|------|
| 新增 | `qrclaw/memory/wiki/manager.py` | WikiMemoryManager，整合 wiki + 兼容接口 |
| 修改 | `qrclaw/memory/__init__.py` | 导出 WikiMemoryManager，移除旧 core/ 引用 |
| 修改 | `qrclaw/memory/long_term.py` | 兼容层，内部调用 WikiMemoryManager |
| 修改 | `qrclaw/workspace.py` | get_memory_manager() 返回 WikiMemoryManager |

### 四、迁移步骤建议

1. **删除旧目录**：先删除 `core/`、`storage/`、`types/`、`entrypoint/`、`factory/`、`context/`、`compression/`
2. **创建新文件**：新建 `wiki/manager.py`
3. **更新导出**：`memory/__init__.py` 改为导出 WikiMemoryManager
4. **迁移兼容**：`long_term.py` 改为兼容层
5. **更新 workspace**：修改 `get_memory_manager()` 返回类型
6. **运行测试**：执行 `python -m pytest qrclaw/tests/ -v` 验证

### 五、关键设计决策

1. **单例模式**：WikiMemory.for_workspace(memory_dir) 按目录缓存
2. **index.json 作为 source of truth**：index.md 由它生成
3. **向后兼容**：LongTermMemory 作为 WikiMemoryManager 别名保留
4. **system prompt 注入**：WikiMemoryManager.load_index() 返回 index.md 全文

---

## 产出了哪些文件

| 文件 | 类型 |
|------|------|
| `/Users/fuqingrong/Documents/agent开发/qrclaw/docs/wiki系统重构设计.md` | 新建（设计方案文档） |

---

## 异常与移交建议

- 无错误，可进入执行阶段
- 建议下一步：执行删除旧目录 + 创建新文件 + 更新导入路径
