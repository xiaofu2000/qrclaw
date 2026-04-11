# Wiki 图查询热更新方案

> 设计日期：2026-04-11
> 目标：为 QRClaw Wiki 记忆系统增加图查询能力，支持热更新

---

## 一、背景与目标

### 1.1 现有 Wiki 系统局限

当前 WikiMemory 虽有 `related`/`tags` 字段存储页面关联，但：
- 无图查询能力（无法查询"谁关联了我"）
- 无图遍历能力（无法查找关联路径）
- IndexManager 每次更新都全量重建 index.md（性能损耗）

### 1.2 目标

1. **热更新机制**：文件监控 + 增量 diff，不重载整个系统
2. **图查询接口**：支持 related/tags 的正向/反向/路径查询
3. **增量边维护**：related/tags 变化时只更新受影响的边

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      WikiMemory                                  │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐   ┌─────────────┐   ┌────────────────────────┐ │
│  │ FileWatcher │   │  GraphCache │   │     QueryInterface     │ │
│  │  (watchdog) │   │  (增量边表) │   │  get_related_pages()  │ │
│  └─────────────┘   └─────────────┘   │  get_backlinks()       │ │
│         ↓               ↑            │  get_by_tag()          │ │
│   pages/*.md        图边增量更新       │  find_path()           │ │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `FileWatcher` | `graph_watcher.py` | watchdog 监听 pages/ 目录变化 |
| `GraphCache` | `graph_cache.py` | 增量边表，维护 related/tags 图结构 |
| `QueryInterface` | 集成到 `WikiMemory` | 对外提供图查询 API |

---

## 三、文件监控机制（FileWatcher）

### 3.1 技术选型

使用 `watchdog` 库（跨平台文件监控）：

```python
# qrclaw/memory/wiki/graph_watcher.py
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class WikiPageHandler(FileSystemEventHandler):
    """处理 pages/ 目录下 .md 文件的增删改事件"""

    def __init__(self, wiki_memory: WikiMemory, graph_cache: GraphCache):
        self.wiki = wiki_memory
        self.graph = graph_cache

    def on_created(self, event):
        if event.src_path.endswith('.md'):
            self._handle_create(event.src_path)

    def on_modified(self, event):
        if event.src_path.endswith('.md'):
            self._handle_modify(event.src_path)

    def on_deleted(self, event):
        if event.src_path.endswith('.md'):
            self._handle_delete(event.src_path)
```

### 3.2 防抖机制

避免连续修改触发多次事件：

```python
import threading
from functools import update_wrapper

class DebouncedHandler(FileSystemEventHandler):
    def __init__(self, handler, delay: float = 0.5):
        self.handler = handler
        self.delay = delay
        self._timers: dict[str, threading.Timer] = {}

    def _debounce(self, key: str, func, *args):
        if key in self._timers:
            self._timers[key].cancel()
        self._timers[key] = threading.Timer(self.delay, func, args)
        self._timers[key].start()
```

### 3.3 启动/停止接口

```python
class FileWatcher:
    def __init__(self, wiki_memory: WikiMemory, graph_cache: GraphCache):
        self.observer = Observer()
        self.handler = DebouncedHandler(
            WikiPageHandler(wiki_memory, graph_cache)
        )

    def start(self):
        """启动文件监控（后台线程）"""
        self.observer.schedule(
            self.handler,
            path=str(wiki.memory_dir / "pages"),
            recursive=False
        )
        self.observer.start()

    def stop(self):
        """停止监控"""
        self.observer.stop()
        self.observer.join()
```

---

## 四、增量节点更新（GraphCache）

### 4.1 图边数据结构

```python
# qrclaw/memory/wiki/graph_cache.py

@dataclass
class GraphEdge:
    """图的边"""
    from_page: str      # 源页面
    to_page: str        # 目标页面
    edge_type: str      # "related" | "tag"
    tag: str = ""       # 当 edge_type="tag" 时，记录具体 tag

@dataclass
class PageNode:
    """图的节点"""
    name: str
    tags: set[str]
    related: set[str]   # related 关联的页面名集合
    updated_at: datetime
```

### 4.2 GraphCache 实现

```python
class GraphCache:
    """
    增量图缓存
    - 维护 pages/*.md 的图结构
    - 支持增量更新（新增/删除/修改节点的边）
    - 线程安全
    """

    def __init__(self):
        self._nodes: dict[str, PageNode] = {}
        self._tag_index: dict[str, set[str]] = {}   # tag -> pages
        self._related_edges: dict[str, set[str]] = {}  # page -> related pages
        self._lock = threading.RLock()

    def load_from_pages_dir(self, pages_dir: Path):
        """从 pages/ 目录批量加载，构建初始图"""
        for md_file in pages_dir.glob("*.md"):
            page = WikiPage.from_markdown(
                md_file.read_text(encoding="utf-8"),
                fallback_name=md_file.stem
            )
            if page:
                self.add_node(page)

    def add_node(self, page: WikiPage):
        """添加或更新节点"""
        with self._lock:
            node = PageNode(
                name=page.name,
                tags=set(page.tags),
                related=set(page.related),
                updated_at=page.updated_at
            )
            self._nodes[page.name] = node
            self._rebuild_index(page.name, node)

    def remove_node(self, name: str):
        """删除节点"""
        with self._lock:
            if name not in self._nodes:
                return

            node = self._nodes.pop(name)

            # 清理 tag 索引
            for tag in node.tags:
                if tag in self._tag_index:
                    self._tag_index[tag].discard(name)

            # 清理 related 边
            if name in self._related_edges:
                del self._related_edges[name]

    def update_edges(self, name: str, added_tags=None, removed_tags=None,
                     added_related=None, removed_related=None):
        """增量更新边（不重载整个节点）"""
        with self._lock:
            # 更新 tag 索引
            for tag in (added_tags or []):
                self._tag_index.setdefault(tag, set()).add(name)

            for tag in (removed_tags or []):
                if tag in self._tag_index:
                    self._tag_index[tag].discard(name)

            # 更新 related 边
            self._related_edges.setdefault(name, set()).update(added_related or [])
            for r in (removed_related or []):
                self._related_edges[name].discard(r)

    def _rebuild_index(self, name: str, node: PageNode):
        """重建节点相关的索引"""
        # tag -> pages
        for tag in node.tags:
            self._tag_index.setdefault(tag, set()).add(name)

        # related edges
        self._related_edges[name] = node.related.copy()
```

---

## 五、图查询接口设计

### 5.1 接口定义

扩展 `WikiMemory` 类，新增以下方法：

```python
class WikiMemory:
    # ... 现有代码 ...

    # ── 图查询接口 ──────────────────────────────────────────────────────────

    def get_related_pages(self, name: str, depth: int = 1) -> list[WikiPage]:
        """
        获取页面直接关联的页面（正向查询）

        Args:
            name: 页面名
            depth: 关联深度，1=直接关联，2=关联的关联（可选扩展）

        Returns:
            WikiPage 列表，按 related 字段顺序返回
        """
        page = self.get_page(name)
        if not page:
            return []

        results = []
        for related_name in page.related:
            related_page = self.get_page(related_name)
            if related_page:
                results.append(related_page)
        return results

    def get_backlinks(self, name: str) -> list[WikiPage]:
        """
        获取反向链接（谁关联了我）

        遍历所有页面，检查其 related 字段是否包含 name
        使用 GraphCache 加速
        """
        if not hasattr(self, '_graph_cache'):
            self._graph_cache = GraphCache()
            self._graph_cache.load_from_pages_dir(self.pages_dir)

        # 反向查找：从 related_edges 中找包含 name 的边
        backlinks = []
        cache = self._graph_cache

        with cache._lock:
            for from_page, to_pages in cache._related_edges.items():
                if name in to_pages:
                    page = self.get_page(from_page)
                    if page:
                        backlinks.append(page)

        return backlinks

    def get_by_tag(self, tag: str) -> list[WikiPage]:
        """
        按标签查询所有页面

        使用 GraphCache 的 _tag_index 加速
        """
        if not hasattr(self, '_graph_cache'):
            self._init_graph_cache()

        cache = self._graph_cache
        with cache._lock:
            page_names = cache._tag_index.get(tag, set()).copy()

        return [self.get_page(name) for name in page_names if self.page_exists(name)]

    def find_path(self, from_page: str, to_page: str, max_depth: int = 3) -> list[str]:
        """
        查找两个页面之间的关联路径（BFS）

        Args:
            from_page: 起始页面
            to_page: 目标页面
            max_depth: 最大搜索深度

        Returns:
            页面名路径列表，如 ["A", "B", "C"]
            未找到返回空列表
        """
        if not hasattr(self, '_graph_cache'):
            self._init_graph_cache()

        cache = self._graph_cache
        visited = {from_page}
        queue = deque([(from_page, [from_page])])

        while queue:
            current, path = queue.popleft()

            if len(path) > max_depth:
                continue

            # 获取当前页面的关联节点
            neighbors = set()
            if current in cache._related_edges:
                neighbors.update(cache._related_edges[current])
            if current in cache._nodes:
                for tag in cache._nodes[current].tags:
                    if tag in cache._tag_index:
                        neighbors.update(cache._tag_index[tag])

            for neighbor in neighbors:
                if neighbor == to_page:
                    return path + [neighbor]

                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))

        return []

    def _init_graph_cache(self):
        """延迟初始化图缓存"""
        if not hasattr(self, '_graph_cache'):
            self._graph_cache = GraphCache()
            self._graph_cache.load_from_pages_dir(self.pages_dir)
```

### 5.2 与现有 WikiMemory 的集成

集成点：

```python
class WikiMemory:
    def __init__(self, memory_dir: Path = None):
        # ... 现有初始化 ...
        self._graph_cache: Optional[GraphCache] = None
        self._watcher: Optional[FileWatcher] = None

    def enable_hot_update(self):
        """启用热更新（启动文件监控 + 图缓存）"""
        if self._watcher is None:
            self._graph_cache = GraphCache()
            self._graph_cache.load_from_pages_dir(self.pages_dir)
            self._watcher = FileWatcher(self, self._graph_cache)
            self._watcher.start()

    def disable_hot_update(self):
        """禁用热更新"""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
            self._graph_cache = None

    # save_page 时增量更新图缓存
    def save_page(self, name, content, ...):
        # ... 现有逻辑 ...

        # 增量更新图缓存
        if hasattr(self, '_graph_cache'):
            existing = self.get_page(name)
            if existing:
                # 计算 diff
                added_tags = set(tags) - set(existing.tags)
                removed_tags = set(existing.tags) - set(tags)
                added_related = set(related) - set(existing.related)
                removed_related = set(existing.related) - set(related)
                self._graph_cache.update_edges(
                    name, added_tags, removed_tags,
                    added_related, removed_related
                )
            else:
                self._graph_cache.add_node(page)
```

---

## 六、热更新流程

### 6.1 文件变化处理流程

```
pages/*.md 变化
    │
    ├─ Created ─→ 读取文件 → WikiPage.from_markdown() → GraphCache.add_node()
    │
    ├─ Modified ─→ 读取文件 → 计算 diff( tags, related )
    │              → GraphCache.update_edges(增量更新)
    │              → IndexManager.upsert() (只更新单条)
    │
    └─ Deleted ─→ GraphCache.remove_node()
                  → IndexManager.remove()
```

### 6.2 增量 vs 全量对比

| 操作 | 全量方案 | 增量方案 |
|------|---------|---------|
| 新增页面 | 重建 index.json + index.md | 只 upsert 单条 + add_node |
| 修改页面 | 重建 index.json + index.md | 只更新变化的 tags/related + update_edges |
| 删除页面 | 重建 index.json + index.md | 只 remove 单条 + remove_node |
| 查询 | 遍历所有文件 | 直接查内存索引 |

---

## 七、文件清单

新增文件：

| 文件路径 | 职责 |
|---------|------|
| `qrclaw/memory/wiki/graph_cache.py` | 图缓存实现（节点、边、索引） |
| `qrclaw/memory/wiki/graph_watcher.py` | 文件监控（watchdog 封装） |
| `qrclaw/memory/wiki/query.py` | 图查询接口（可集成到 WikiMemory） |

修改文件：

| 文件路径 | 改动 |
|---------|------|
| `qrclaw/memory/wiki/wiki_memory.py` | 集成热更新开关、增量更新逻辑 |

---

## 八、使用示例

```python
from qrclaw.memory.wiki import WikiMemory

# 创建 WikiMemory
wiki = WikiMemory.for_workspace(Path("/path/to/memory"))

# 启用热更新
wiki.enable_hot_update()

# 保存页面（自动更新图缓存）
wiki.save_page("QRClaw架构", content="...", tags=["架构"], related=["记忆系统"])

# 图查询
related = wiki.get_related_pages("QRClaw架构")
backlinks = wiki.get_backlinks("记忆系统")
architecture_pages = wiki.get_by_tag("架构")
path = wiki.find_path("用户操作偏好", "Memory 系统设计")

# 禁用热更新
wiki.disable_hot_update()
```

---

## 九、后续扩展

1. **路径查询优化**：使用 NetworkX 替代手写 BFS
2. **图持久化**：将 GraphCache 定期刷回 disk（JSON 格式）
3. **图可视化**：导出 dot/graphviz 格式
4. **图订阅**：文件变化时触发回调（Observer 模式）
