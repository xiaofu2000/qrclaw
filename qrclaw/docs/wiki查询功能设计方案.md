# Wiki 查询功能设计方案

> 文档版本：v1.0  
> 创建时间：2026-04-12  
> 状态：草稿

---

## 一、现状分析

### 1.1 现有查询接口

WikiMemory 类提供了以下查询方法：

| 方法 | 功能 | 限制 |
|------|------|------|
| `list_pages()` | 从 pages/ 目录扫描所有 .md 文件，返回 WikiPage 列表 | 无过滤、无分页 |
| `search_pages(query)` | 关键词全文搜索（name → description → content 优先级） | 朴素包含匹配，无语义理解 |
| `fuzzy_find_name(name)` | 模糊匹配页面名（忽略大小写和空格） | 仅匹配单个页面 |
| `get_entry(name)` / `all_entries()` | 从 index.json 快速读取元数据 | 不读页面正文 |

### 1.2 索引机制

```
memory_dir/
├── index.json    # 结构化索引（source of truth）
├── index.md      # 人类可读目录（供 system prompt 注入）
├── log.md        # append-only 操作日志
└── pages/
    ├── 页面1.md
    └── 页面2.md
```

index.json 存储页面元数据：`name, file, description, tags, related, created_at, updated_at`

### 1.3 现有能力短板

| 短板 | 说明 |
|------|------|
| 无维度过滤 | 无法按 tags/related/时间范围筛选 |
| 无语义搜索 | 仅支持朴素字符串包含匹配 |
| 无分页 | 全量返回 |
| 无排序 | 仅按 name > description > content 三级粗排 |
| 无相关推荐 | 无法基于 tags 或 related 字段推荐关联页面 |

---

## 二、需求场景

### 2.1 按标签检索
- 用户需要查找所有带有特定标签（如 "架构"）的页面
- 支持多标签组合查询（AND/OR）
- 示例：`tag:架构 AND tag:核心`

### 2.2 按内容搜索
- 关键词搜索页面标题、描述、正文
- 支持按字段优先级排序（title > description > content）
- 示例：搜索 "Router" 相关内容

### 2.3 相关页面推荐
- 基于当前页面的 tags 或 related 字段，推荐相似页面
- 扩展：根据内容相似度推荐（需增强方案）

### 2.4 分页与排序
- 大数据量下需要分页返回（limit/offset）
- 支持按更新时间、创建时间、名称排序
- 示例：`order_by: updated_at DESC, limit: 10, offset: 20`

### 2.5 时间范围查询
- 查询特定时间段内创建或更新的页面
- 示例：`created_after: 2026-01-01, created_before: 2026-04-01`

---

## 三、低配方案设计

### 3.1 设计目标
基于现有 `IndexManager.all_entries()` 和 `list_pages()` 扩展过滤/排序/分页能力，**不引入外部依赖**。

### 3.2 核心接口设计

```python
class WikiQuery:
    """Wiki 查询构造器，支持链式调用"""
    
    def __init__(self, memory: WikiMemory):
        self._memory = memory
        self._filters = []
        self._sort_field = "updated_at"
        self._sort_order = "DESC"
        self._limit = 20
        self._offset = 0
    
    def tag(self, tag: str, mode: str = "AND") -> "WikiQuery":
        """按标签过滤，mode 支持 AND/OR"""
        self._filters.append(("tag", tag, mode))
        return self
    
    def related(self, name: str) -> "WikiQuery":
        """按关联页面过滤"""
        self._filters.append(("related", name, "AND"))
        return self
    
    def keyword(self, kw: str, fields: list[str] = None) -> "WikiQuery":
        """关键词搜索，fields 指定搜索字段"""
        self._filters.append(("keyword", kw, fields or ["name", "description", "content"]))
        return self
    
    def time_range(self, field: str, start: datetime = None, end: datetime = None) -> "WikiQuery":
        """时间范围过滤，field 支持 created_at/updated_at"""
        self._filters.append(("time_range", field, start, end))
        return self
    
    def order_by(self, field: str, desc: bool = True) -> "WikiQuery":
        """排序，field 支持 name/created_at/updated_at"""
        self._sort_field = field
        self._sort_order = "DESC" if desc else "ASC"
        return self
    
    def paginate(self, limit: int, offset: int = 0) -> "WikiQuery":
        """分页"""
        self._limit = limit
        self._offset = offset
        return self
    
    def execute(self) -> QueryResult:
        """执行查询，返回分页结果"""
        # Step 1: 获取全量数据（优先用 index.json）
        entries = self._load_entries()
        
        # Step 2: 应用过滤
        entries = self._apply_filters(entries)
        
        # Step 3: 排序
        entries = self._apply_sort(entries)
        
        # Step 4: 分页
        total = len(entries)
        entries = entries[self._offset: self._offset + self._limit]
        
        # Step 5: 填充正文（可选，按需加载）
        return QueryResult(items=entries, total=total, limit=self._limit, offset=self._offset)


@dataclass
class QueryResult:
    items: list[dict]       # 查询结果列表
    total: int              # 总数
    limit: int              # 每页条数
    offset: int             # 偏移量
    has_more: bool          # 是否有下一页
```

### 3.3 实现要点

#### 3.3.1 数据加载策略
```python
def _load_entries(self) -> list[dict]:
    """优先从 index.json 加载，index.json 无 content 时再读 pages/"""
    index_entries = self._memory.index_manager.all_entries()
    
    # 如果有 content 相关过滤，需要补充加载正文
    if self._needs_content():
        for entry in index_entries:
            page = self._memory.get_page(entry["name"])
            entry["content"] = page.content
    return index_entries

def _needs_content(self) -> bool:
    """检查是否需要加载页面正文"""
    return any(f[0] in ("keyword",) for f in self._filters)
```

#### 3.3.2 过滤实现
```python
def _apply_filters(self, entries: list[dict]) -> list[dict]:
    for filter_type, *args in self._filters:
        if filter_type == "tag":
            tag, mode = args
            entries = [e for e in entries if self._match_tag(e, tag, mode)]
        elif filter_type == "related":
            name, = args
            entries = [e for e in entries if name in e.get("related", [])]
        elif filter_type == "keyword":
            kw, fields = args
            entries = self._keyword_filter(entries, kw, fields)
        elif filter_type == "time_range":
            field, start, end = args
            entries = self._time_filter(entries, field, start, end)
    return entries

def _match_tag(self, entry: dict, tag: str, mode: str) -> bool:
    tags = entry.get("tags", [])
    if mode == "AND":
        return tag in tags
    else:  # OR
        return any(t == tag for t in tags)
```

#### 3.3.3 相关页面推荐
```python
def recommend_related(self, page_name: str, limit: int = 5) -> list[dict]:
    """基于 tags 和 related 字段推荐关联页面"""
    entry = self._memory.index_manager.get_entry(page_name)
    if not entry:
        return []
    
    # 1. 直接关联（related 字段）
    direct = [self._memory.index_manager.get_entry(r) for r in entry.get("related", [])]
    
    # 2. 同标签页面（排除自身和直接关联）
    tag_matches = []
    for e in self._memory.index_manager.all_entries():
        if e["name"] != page_name and e["name"] not in entry.get("related", []):
            if set(entry.get("tags", [])) & set(e.get("tags", [])):
                tag_matches.append(e)
    
    # 合并并限制数量
    related = direct + tag_matches
    return related[:limit]
```

### 3.4 API 层设计

在 `tools/wiki_tools.py` 中新增查询工具：

```python
@register_tool(name="query_wiki", description="查询 Wiki 知识库")
def query_wiki(
    tags: list[str] = None,        # 标签列表
    keyword: str = None,            # 关键词
    related_to: str = None,         # 关联页面名
    order_by: str = "updated_at",   # 排序字段
    order_desc: bool = True,        # 降序
    limit: int = 20,                # 每页条数
    offset: int = 0,                # 偏移量
    include_content: bool = False, # 是否包含正文
) -> dict:
    """
    查询 Wiki 知识库
    
    示例：
    - 查询所有"架构"标签页面：tags=["架构"]
    - 搜索关键词"Router"：keyword="Router"
    - 分页获取第2页：limit=10, offset=10
    """
    query = WikiQuery(wiki_memory)
    
    if tags:
        for tag in tags:
            query.tag(tag)
    if keyword:
        query.keyword(keyword)
    if related_to:
        query.related(related_to)
    if order_by:
        query.order_by(order_by, order_desc)
    if limit or offset:
        query.paginate(limit, offset)
    
    result = query.execute()
    
    # 按需过滤正文
    if not include_content:
        for item in result.items:
            item.pop("content", None)
    
    return asdict(result)


@register_tool(name="recommend_wiki_pages", description="推荐相关 Wiki 页面")
def recommend_wiki_pages(
    page_name: str,
    limit: int = 5,
) -> list[dict]:
    """推荐与指定页面相关的其他页面"""
    return wiki_memory.recommend_related(page_name, limit)
```

### 3.5 低配方案文件变更

| 操作 | 文件路径 | 说明 |
|------|----------|------|
| 新增 | `memory/wiki/query.py` | WikiQuery 查询构造器 |
| 修改 | `memory/wiki/wiki_memory.py` | 新增 `recommend_related()` 方法 |
| 修改 | `tools/wiki_tools.py` | 新增 `query_wiki`、`recommend_wiki_pages` 工具 |
| 新增 | `tests/test_wiki_query.py` | 单元测试 |

---

## 四、增强方案设计

### 4.1 设计目标
引入向量数据库实现语义搜索，支持自然语言查询，理解语义相关性。

### 4.2 技术选型

| 向量数据库 | 优势 | 适用场景 |
|------------|------|----------|
| **Qdrant** | 轻量、Python SDK 完善、支持本地模式 | 中小规模（<100万向量） |
| **ChromaDB** | 零配置、嵌入式 | 简单场景、快速原型 |
| **Milvus** | 分布式、高可用 | 大规模生产环境 |
| **pgvector** | 复用 PostgreSQL，无需新组件 | 已有 PG 环境 |

**推荐**：Qdrant（轻量 + 本地模式 + 成熟）

### 4.3 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent / 调用方                          │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    WikiQuery (低配)                          │
│  filter / sort / paginate / tag query                       │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                 VectorSearchIndex (增强)                     │
│  semantic_search(query, top_k, filter)                       │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                      Qdrant / ChromaDB                       │
│           (向量存储 + 余弦相似度检索)                         │
└─────────────────────────────────────────────────────────────┘
```

### 4.4 核心接口

```python
class VectorSearchIndex:
    """向量搜索索引"""
    
    def __init__(self, memory: WikiMemory, vector_store: str = "qdrant"):
        self._memory = memory
        self._client = self._init_client(vector_store)
        self._collection = "wiki_pages"
    
    def _init_client(self, store: str):
        if store == "qdrant":
            from qdrant_client import QdrantClient
            return QdrantClient(path="./data/qdrant")  # 本地模式
        elif store == "chroma":
            import chromadb
            return chromadb.Client()
        raise ValueError(f"Unsupported vector store: {store}")
    
    def index_page(self, page: WikiPage):
        """为单个页面建立向量索引"""
        embedding = self._get_embedding(page.content)
        self._client.upsert(
            collection=self._collection,
            points=[{
                "id": page.name,
                "vector": embedding,
                "payload": {
                    "name": page.name,
                    "description": page.description,
                    "tags": page.tags,
                    "content": page.content,
                }
            }]
        )
    
    def rebuild_index(self):
        """重建全量索引"""
        # 删除旧 collection
        self._client.delete_collection(self._collection)
        self._client.create_collection(self._collection)
        
        # 遍历所有页面
        for page in self._memory.list_pages():
            self.index_page(page)
    
    def semantic_search(
        self,
        query: str,
        top_k: int = 10,
        tags: list[str] = None,
    ) -> list[dict]:
        """语义搜索"""
        # 1. 查询向量
        query_embedding = self._get_embedding(query)
        
        # 2. 搜索
        results = self._client.search(
            collection=self._collection,
            query_vector=query_embedding,
            limit=top_k,
            query_filter=self._build_filter(tags) if tags else None,
        )
        
        # 3. 格式化结果
        return [
            {
                "name": r.payload["name"],
                "description": r.payload["description"],
                "tags": r.payload["tags"],
                "score": r.score,  # 相似度得分
                "excerpt": self._make_excerpt(r.payload["content"], query),
            }
            for r in results
        ]
    
    def _get_embedding(self, text: str) -> list[float]:
        """调用 Embedding API 获取向量"""
        # 支持 OpenAI / 本地模型（如 sentence-transformers）
        from openai import OpenAI
        client = OpenAI()
        resp = client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return resp.data[0].embedding
    
    def _make_excerpt(self, content: str, query: str, context_chars: int = 200) -> str:
        """从正文中提取包含关键词的片段"""
        idx = content.lower().find(query.lower())
        if idx == -1:
            return content[:context_chars] + "..."
        start = max(0, idx - context_chars // 2)
        end = min(len(content), idx + len(query) + context_chars // 2)
        return ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
```

### 4.5 自动同步机制

```python
class WikiMemoryWithVectorIndex(WikiMemory):
    """带向量索引的 WikiMemory"""
    
    def __init__(self, *args, vector_store: str = "qdrant", **kwargs):
        super().__init__(*args, **kwargs)
        self._vector_index = VectorSearchIndex(self, vector_store)
    
    def save_page(self, page: WikiPage):
        super().save_page(page)
        self._vector_index.index_page(page)  # 自动同步向量
    
    def delete_page(self, name: str):
        super().delete_page(name)
        self._vector_index.delete_page(name)
    
    def semantic_search(self, query: str, top_k: int = 10, tags: list[str] = None) -> list[dict]:
        """语义搜索接口"""
        return self._vector_index.semantic_search(query, top_k, tags)
```

### 4.6 增强方案文件变更

| 操作 | 文件路径 | 说明 |
|------|----------|------|
| 新增 | `memory/wiki/vector_index.py` | VectorSearchIndex 向量索引类 |
| 新增 | `memory/wiki/wiki_memory_with_vector.py` | 带向量索引的 WikiMemory |
| 修改 | `tools/wiki_tools.py` | 新增 `semantic_search_wiki` 工具 |
| 新增 | `tests/test_vector_index.py` | 向量索引测试 |

### 4.7 配置项

```yaml
# config/wiki.yaml
vector_search:
  enabled: false                    # 是否启用向量搜索
  provider: "qdrant"               # qdrant / chroma / pgvector
  embedding_model: "text-embedding-3-small"
  local_path: "./data/qdrant"      # 本地存储路径
  auto_sync: true                   # 保存页面时自动同步索引
```

---

## 五、实施建议和优先级

### 5.1 优先级矩阵

| 优先级 | 功能 | 工作量 | 价值 | 说明 |
|--------|------|--------|------|------|
| **P0** | 分页与排序 | 低 | 高 | 所有查询的基础能力 |
| **P0** | 按标签过滤 | 低 | 高 | 高频查询场景 |
| **P0** | 相关页面推荐 | 低 | 中 | 提升知识发现能力 |
| **P1** | 时间范围查询 | 低 | 中 | 特定场景需求 |
| **P1** | 关键词高亮/摘要 | 中 | 中 | 提升搜索结果可读性 |
| **P2** | 向量语义搜索 | 高 | 高 | 核心差异化能力 |

### 5.2 分阶段实施计划

#### 阶段一：低配能力完善（1-2天）
- 实现 `WikiQuery` 查询构造器
- 支持分页、排序、标签过滤
- 实现 `recommend_related()` 推荐方法
- 暴露 `query_wiki` 工具给 Agent

#### 阶段二：增强体验（1天）
- 支持时间范围查询
- 关键词高亮和摘要提取
- `query_wiki` 工具完善

#### 阶段三：向量搜索（2-3天）
- 集成 Qdrant 或 ChromaDB
- 实现自动同步机制
- 暴露 `semantic_search_wiki` 工具
- 性能测试与调优

### 5.3 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| 向量数据库运维复杂度 | 中 | 中 | 优先使用 Qdrant 本地模式，简化部署 |
| Embedding API 成本 | 中 | 中 | 支持本地模型（如 sentence-transformers） |
| 索引同步一致性 | 低 | 高 | 使用事务 + 定期全量重建 |
| 大数据量性能 | 中 | 中 | 分页限制（默认20条）+ 懒加载正文 |

---

## 六、附录

### 6.1 WikiPage 数据结构

```python
@dataclass
class WikiPage:
    name: str
    content: str           # Markdown 正文
    description: str      # 一句话描述
    tags: list[str]       # 标签列表
    related: list[str]    # 关联页面名
    created_at: datetime
    updated_at: datetime
```

### 6.2 index.json 结构

```json
{
  "pages": [
    {
      "name": "qrclaw架构",
      "file": "qrclaw架构.md",
      "description": "QRClaw Agent 的整体执行架构",
      "tags": ["架构", "核心"],
      "related": ["记忆系统", "Router节点"],
      "created_at": "2026-04-10T00:00:00",
      "updated_at": "2026-04-10T12:00:00"
    }
  ]
}
```

### 6.3 参考实现

- 当前 WikiMemory 源码：`memory/wiki/wiki_memory.py`
- 当前 IndexManager 源码：`memory/wiki/index.py`
- 现有工具层：`tools/wiki_tools.py`
