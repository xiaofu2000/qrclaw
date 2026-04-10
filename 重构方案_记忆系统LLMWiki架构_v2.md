# QRClaw 记忆系统重构方案 v2：LLM Wiki 架构

**版本**: v2.0
**日期**: 2026-04-10
**状态**: 待实施

---

## 第一章 核心思想

### 1.1 现有方案的问题

现有记忆系统是**条目式**的：每次提取一条孤立的记忆，存进 `user/feedback/project/reference` 四个分类桶里。记忆之间没有关联，MEMORY.md 只是一个平铺列表。

问题：
- 记忆是碎片化的，同一个主题分散在多条记录里
- 没有跨记忆的关联，Agent 无法顺藤摸瓜找到相关知识
- 同一主题的新内容会新建一条记忆而不是更新旧的（重复堆积）
- MEMORY.md 随时间变成垃圾堆

### 1.2 LLM Wiki 的核心思想

**不是检索，是编译。**

```
传统 RAG:
  原始内容 → 每次查询时检索 → LLM 实时推导答案
  知识不积累，每次从零开始

LLM Wiki:
  原始内容 → LLM 一次性整合 → 持久化 Wiki 页面
  知识持续累积，越用越丰富
```

Wiki 是一个**持续累积、自我维护的知识产物**：
- 新内容进来，LLM 不只是新建一条记录，而是读取相关已有页面、更新它们、建立交叉引用
- 页面之间用 `[[页面名]]` 互相链接，形成知识网络
- 矛盾和过时信息被主动标记和修正

### 1.3 对 QRClaw 的意义

Agent 执行完任务后：

```
现在：
  提取 → 新建一条"会话记忆_20260410_010058.md" → 写进 project/ 目录

改后：
  提取 → 判断涉及哪些页面（如"qrclaw架构"、"记忆系统"）
       → 更新已有页面，补充新发现
       → 在页面间建立链接
       → 更新 index.md 和 index.json
```

---

## 第二章 目录结构

### 2.1 新目录

```
~/.qrclaw/agents/{agent_id}/memory/
├── index.md          ← 所有页面的人类可读目录（给 LLM 注入 system prompt）
├── index.json        ← 结构化索引（给代码/脚本使用）
├── log.md            ← append-only 操作日志
└── pages/            ← 所有 Wiki 页面（平铺，不分子目录）
    ├── overview.md   ← 整体综述页（自动维护）
    └── [任意].md     ← LLM 自由创建和更新
```

**废弃**：`user/`、`feedback/`、`project/`、`reference/` 四个子目录全部废弃。

### 2.2 和现有结构对比

| 现有 | 新 |
|---|---|
| `memory/MEMORY.md` | `memory/index.md` + `memory/index.json` |
| `memory/user/*.md` | `memory/pages/*.md` |
| `memory/feedback/*.md` | `memory/pages/*.md` |
| `memory/project/*.md` | `memory/pages/*.md` |
| `memory/reference/*.md` | `memory/pages/*.md` |
| 无 | `memory/log.md` |

---

## 第三章 文件格式

### 3.1 Wiki 页面格式（`pages/*.md`）

```markdown
---
name: qrclaw架构
description: QRClaw Agent 的整体执行架构
tags: [架构, 核心, graph]
related: [记忆系统, Router节点, Replanner节点, ContextManager]
created_at: 2026-04-10T00:00:00
updated_at: 2026-04-10T12:00:00
---

# qrclaw架构

QRClaw 是一个本地 AI Agent 框架，核心执行链路为：

用户输入 → [[Router节点]] → 判断路由
  - route=direct → ReactLoop 直接执行
  - route=plan   → [[PlanExecutor]] → [[Replanner节点]] → ReactLoop 汇总

## 记忆系统

参见 [[记忆系统]]，采用 LLM Wiki 架构，多页面互联。

## ContextManager

所有节点通过 [[ContextManager]] 线程单例访问上下文，不直接传参。
```

**规则：**
- frontmatter 有 `name`、`description`、`tags`、`related`、`created_at`、`updated_at`
- `related` 是关联页面名列表（代码维护）
- 正文里用 `[[页面名]]` 建立可读链接（LLM 维护）
- 两者可以不完全一致，`related` 是结构化的，`[[]]` 是叙述性的

### 3.2 index.md 格式

```markdown
# QRClaw Wiki 索引

> 最后更新: 2026-04-10 12:00:00 | 共 12 个页面

## 核心架构
- [qrclaw架构](pages/qrclaw架构.md) — QRClaw Agent 的整体执行架构
- [记忆系统](pages/记忆系统.md) — LLM Wiki 风格多页面记忆

## 节点
- [Router节点](pages/Router节点.md) — 路由判断，生成执行计划
- [Replanner节点](pages/Replanner节点.md) — 动态重规划

## 用户
- [用户信息](pages/用户信息.md) — 用户背景、偏好

## 未分类
- [xxx](pages/xxx.md) — ...
```

**规则：**
- 页面按 `tags` 自动归组
- LLM 读这个文件来了解 Wiki 全貌
- 由代码从 `index.json` 自动生成，不需要 LLM 手动维护

### 3.3 index.json 格式

```json
{
  "version": "2.0",
  "updated_at": "2026-04-10T12:00:00",
  "pages": [
    {
      "name": "qrclaw架构",
      "file": "pages/qrclaw架构.md",
      "description": "QRClaw Agent 的整体执行架构",
      "tags": ["架构", "核心", "graph"],
      "related": ["记忆系统", "Router节点", "Replanner节点"],
      "created_at": "2026-04-10T00:00:00",
      "updated_at": "2026-04-10T12:00:00"
    }
  ]
}
```

**用途：**
- 代码层快速查找页面（不用解析 Markdown）
- 检查页面是否存在
- 构建 `index.md`
- 未来扩展（搜索、图遍历）

### 3.4 log.md 格式

```markdown
## [2026-04-10 12:00:00] write | qrclaw架构
新建页面，记录 Router→Plan→Replanner 架构

## [2026-04-10 12:30:00] update | 记忆系统
更新：新增 LLM Wiki 章节，关联 [[ContextManager]]

## [2026-04-10 13:00:00] extract | 自动提取
从会话中提取：更新「用户信息」，新建「MiniMax配置」
```

---

## 第四章 代码架构

### 4.1 模块结构

```
qrclaw/memory/
├── __init__.py
├── token_utils.py          ← 不变
├── wiki/                   ← 新增，替代 core/
│   ├── __init__.py
│   ├── wiki_memory.py      ← 主类 WikiMemory（替代 LongTermMemory）
│   ├── page.py             ← WikiPage 数据类（替代 MemoryFile）
│   └── index.py            ← IndexManager（维护 index.md + index.json）
├── core/                   ← 保留但不再对外暴露
│   └── memory_manager.py   ← 内部用，WikiMemory 复用部分逻辑
├── context/                ← 不变
├── compression/            ← 不变
└── types/
    ├── types.py            ← WikiPage 替代 MemoryFile，废弃 MemoryType
    └── frontmatter.py      ← 不变，复用
```

### 4.2 WikiMemory 主类接口

```python
class WikiMemory:
    """LLM Wiki 记忆管理器，替代 LongTermMemory"""

    def save_page(
        self,
        name: str,
        content: str,
        description: str = "",
        tags: List[str] = [],
        related: List[str] = [],
    ) -> WikiPage:
        """
        新建或更新 Wiki 页面（upsert 语义）
        - 页面不存在 → 新建
        - 页面已存在 → 更新内容，保留 created_at，更新 updated_at
        同时更新 index.json → 重建 index.md → 追加 log.md
        """

    def get_page(self, name: str) -> Optional[WikiPage]:
        """获取单个页面"""

    def search_pages(self, query: str) -> List[WikiPage]:
        """关键词搜索（全文）"""

    def list_pages(self) -> List[WikiPage]:
        """列出所有页面"""

    def delete_page(self, name: str) -> bool:
        """删除页面，同步更新索引"""

    def load_index(self) -> str:
        """返回 index.md 内容，注入 system prompt 用"""

    def rebuild_index(self) -> None:
        """从 pages/ 目录重建 index.json 和 index.md"""
```

### 4.3 WikiPage 数据类

```python
@dataclass
class WikiPage:
    name: str
    content: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def filename(self) -> str:
        return self.name + ".md"  # 直接用名字，不 slugify（中文文件名可读性更好）

    @property
    def filepath(self, pages_dir: Path) -> Path:
        return pages_dir / self.filename
```

### 4.4 IndexManager

```python
class IndexManager:
    """维护 index.json 和 index.md 的双向同步"""

    def upsert(self, page: WikiPage) -> None:
        """更新或插入页面索引条目"""

    def remove(self, name: str) -> None:
        """删除索引条目"""

    def rebuild_from_pages(self, pages_dir: Path) -> None:
        """从 pages/ 目录完整重建索引"""

    def rebuild_index_md(self) -> None:
        """从 index.json 重新生成 index.md"""

    def get_entry(self, name: str) -> Optional[dict]:
        """从 index.json 快速查找页面元数据"""
```

---

## 第五章 工具接口

### 5.1 新工具列表

| 工具 | 说明 | 替代 |
|---|---|---|
| `write_wiki_page` | 新建或更新 Wiki 页面 | `write_memory` |
| `read_wiki_page` | 读取指定页面正文 | `read_memory` |
| `list_wiki_pages` | 列出所有页面（含描述） | `list_memory` |
| `search_wiki` | 关键词搜索页面 | `search_memory` |
| `delete_wiki_page` | 删除页面 | `delete_memory` |

### 5.2 write_wiki_page 接口

```python
class WriteWikiPageArgs(BaseModel):
    name: str = Field(description="页面名称，如「qrclaw架构」、「用户偏好」")
    content: str = Field(description="页面正文，Markdown 格式，可用 [[页面名]] 建立链接")
    description: str = Field(description="一句话描述，显示在索引里", default="")
    tags: List[str] = Field(description="标签列表，用于索引分组", default=[])
    related: List[str] = Field(description="关联页面名列表", default=[])
```

---

## 第六章 记忆提取升级

### 6.1 触发逻辑：不变

主动触发和被动触发的条件完全不变：

**主动**：Agent 在执行过程中判断有值得记录的内容，主动调用 `write_wiki_page` 工具。

**被动**：`MemoryExtractionNode` 按现有阈值触发（token 数 + 工具调用次数），在 ReactLoop 结束后后台线程执行。

### 6.2 变的只是写入方式

```
现在：
  触发 → LLM 分析对话 → 新建一条 MemoryFile → 写进 user/project/... 分类目录

改后：
  触发 → LLM 分析对话
       → 读取 index.md（了解已有页面）
       → 判断：新建页面 or 更新已有页面
       → 对每个页面写入（upsert 语义）
       → 维护 [[链接]] 和 related 字段
       → 更新 index.json → 重建 index.md → 追加 log.md
```

### 6.3 提取提示词变化

新增注入 `index.md` 内容，让 LLM 知道已有哪些页面，决定新建还是更新：

```
【现有 Wiki 页面】
{index_md_content}

【最近对话】
{messages}

请分析对话，决定：
1. 需要新建哪些页面（不在上面列表中的新知识）
2. 需要更新哪些已有页面（补充或修正已有页面的内容）
3. 每个页面的 related 关联关系和 [[链接]]

对于要更新的页面，合并新信息后完整写回（不要只写增量）。
```

---

## 第七章 system prompt 变化

### 7.1 现有注入方式

```python
# prompt.py
def _build_memory_section(memory_dir):
    memory = LongTermMemory(memory_dir)
    content = memory.load()  # 返回 MEMORY.md 全文
    return f"## 中期记忆\n{content}"
```

### 7.2 新注入方式

```python
def _build_memory_section(memory_dir):
    wiki = WikiMemory(memory_dir)
    index_content = wiki.load_index()  # 返回 index.md 全文
    return f"## Wiki 记忆索引\n{index_content}\n\n（使用 read_wiki_page 读取具体页面内容）"
```

注入的是 `index.md`（页面目录），不是每个页面的正文。Agent 需要具体内容时调用 `read_wiki_page`。

---

## 第八章 改动范围汇总

### 新增文件

| 文件 | 说明 |
|---|---|
| `memory/wiki/__init__.py` | 模块导出 |
| `memory/wiki/wiki_memory.py` | WikiMemory 主类 |
| `memory/wiki/page.py` | WikiPage 数据类 |
| `memory/wiki/index.py` | IndexManager |
| `tools/wiki_tools.py` | 新工具注册 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `memory/__init__.py` | 导出 WikiMemory，废弃 LongTermMemory |
| `prompt.py` | `_build_memory_section` 改用 WikiMemory |
| `graph/nodes/memory_extraction.py` | 提取逻辑升级，注入 index.md |
| `memory/context/context_manager.py` | `long_term.py` → `wiki_memory.py` |

### 废弃文件（可保留不删，兼容旧数据）

| 文件 | 说明 |
|---|---|
| `memory/core/long_term.py` | 替换为 WikiMemory |
| `tools/memory_tools.py` | 替换为 wiki_tools.py |
| `memory/types/types.py` 中的 `MemoryType` | 废弃四分类 |

### 数据迁移

```bash
# 清空旧数据（开发环境）
rm -rf ~/.qrclaw/agents/*/memory/user/
rm -rf ~/.qrclaw/agents/*/memory/feedback/
rm -rf ~/.qrclaw/agents/*/memory/project/
rm -rf ~/.qrclaw/agents/*/memory/reference/
rm ~/.qrclaw/agents/*/memory/MEMORY.md

# 新结构由 WikiMemory 初始化时自动创建
```

---

## 第九章 实施顺序

1. **WikiPage + IndexManager**（数据层，无依赖）
2. **WikiMemory 主类**（依赖第1步）
3. **wiki_tools.py**（依赖第2步，可测试）
4. **prompt.py 切换**（依赖第2步）
5. **memory_extraction.py 升级**（依赖第3步）
6. **清理旧代码**（最后做，确保新代码跑通后）

---

**文档版本**: v2.0
**状态**: 待确认实施
