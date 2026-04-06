# QRClaw 启动性能分析报告

## 问题描述
历史消息特别多的时候，启动 qrclaw 会特别慢。

---

## 项目架构概览

```
cli.py
  └── agent.run()
        ├── Session(sessions_dir, session_id, resume=True)
        │     └── _load()  ← 启动时加载历史消息
        ├── init_context_manager(session, workspace)
        └── GraphRunner.run()
              ├── Router.run()
              └── ReactLoop.run()
                    └── ctx.compress_if_needed()
                    └── ctx.build_messages("react")  ← 每次迭代调用
```

---

## 核心配置参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `MODEL_MAX_TOKENS` | 128000 | 模型最大上下文窗口 |
| `COMPRESS_THRESHOLD` | 76800 | 60%，超限触发压缩 |
| `COMPRESS_TARGET_MIN_RATIO` | 0.20 | 压缩后最小占比 |
| `COMPRESS_TARGET_MAX_RATIO` | 0.25 | 压缩后最大占比 |

---

## 瓶颈分析

### 瓶颈 1：启动时全量 token 计算（🔴 高优先级）

**位置**: `memory/context/session.py` 第 130-131 行

```python
def _load(self):
    if self._path.exists():
        ...
        self.prompt_tokens = count_tokens(self.messages)  # ← 这里
```

**问题**: 启动时对所有历史消息调用 `count_tokens()`，使用 tiktoken 逐条 encode。

```python
def count_tokens(messages: list[dict]) -> int:
    tokens = 0
    for msg in messages:  # 逐条遍历
        tokens += 4
        for key, value in msg.items():
            if value is not None:
                tokens += len(_encoding.encode(str(value)))  # tiktoken encode
    tokens += 2
    return tokens
```

**影响**: 
- 1000 条消息 × 平均每条 200 字符 ≈ 500+ 次 tiktoken encode
- 这是阻塞操作，无法并行

---

### 瓶颈 2：重复的 tiktoken encoder 初始化（⚡ 中优先级）

**位置**: 
- `memory/context/session.py` 第 10-14 行
- `memory/compression/compressor.py` 第 14-18 行

```python
# session.py
import tiktoken
try:
    _encoding = tiktoken.encoding_for_model(OPENAI_MODEL)
except KeyError:
    _encoding = tiktoken.get_encoding("cl100k_base")

# compressor.py
import tiktoken
try:
    _encoding = tiktoken.encoding_for_model(OPENAI_MODEL)
except KeyError:
    _encoding = tiktoken.get_encoding("cl100k_base")
```

**问题**: 两个模块各自初始化 tiktoken encoder，增加了加载开销。

**影响**: 虽然 tiktoken 加载一次后会被缓存，但重复的初始化逻辑增加了模块加载时间。

---

### 瓶颈 3：System Prompt 每次迭代重建（🔴 高优先级）

**位置**: `memory/context/context_manager.py` 第 95-97 行

```python
def _make_system_prompt(self) -> dict:
    content = build_system_prompt(  # ← 每次调用都重新构建
        heartbeat_file=self.workspace.heartbeat_file,
        ...
    )
    return {"role": "system", "content": content}
```

**问题**: `build_system_prompt()` 包含大量 I/O 操作：

1. **文件读取**（多次）：
   - `agent_file.read_text()` — AGENT.md
   - `heartbeat_file.read_text()` — heartbeat 任务文件
   - `memory.load()` — 中期记忆文件

2. **动态获取**：
   - `get_schemas()` — 获取所有工具 schema
   - `SkillRegistry().load_from_dir()` — 加载所有技能

3. **字符串拼接**：多个 section 拼接构建完整 prompt

**影响**: 
- 每次迭代都重新读取文件和构建 prompt
- 在 ReAct 循环中，每轮都调用 `build_messages("react")`
- 如果有 50 轮迭代，就重建 50 次 system prompt

---

### 瓶颈 4：压缩阈值检查每次迭代（⚡ 中优先级）

**位置**: `graph/nodes/react_loop.py` 第 50 行和第 60 行

```python
for iteration in range(MAX_ITERATIONS):  # 默认 100 次
    ctx.compress_if_needed()  # ← 每次迭代都检查
    messages = ctx.build_messages("react")
    ...
    if response.prompt_tokens > COMPRESS_THRESHOLD:
        ctx.compress_if_needed()  # ← 又检查一次
```

**问题**: `compress_if_needed()` 每次都计算完整 token 数：

```python
def compress_if_needed(self):
    messages = [self._make_system_prompt(), *self.session.messages]
    if count_tokens(messages) > COMPRESS_THRESHOLD:  # ← 重新构建 system prompt + token 计算
        summarize(self.session)
```

**影响**: 每次迭代都：
1. 重新构建 system prompt（见瓶颈 3）
2. 对所有消息计算 token 数

---

### 瓶颈 5：压缩时重复 token 计算（⚡ 中优先级）

**位置**: `memory/compression/compressor.py` 第 79-98 行

```python
def _pick_recent(messages: list[dict], max_tokens: int = None) -> tuple[list[dict], list[dict]]:
    recent = []
    token_count = 0
    for msg in reversed(messages):  # ← 逐条遍历
        t = count_tokens([msg])  # ← 每次调用都创建列表 + encode
        if token_count + t > max_tokens:
            break
        recent.insert(0, msg)  # ← insert(0, ...) 是 O(n) 操作
        token_count += t
```

**问题**:
1. `count_tokens([msg])` 每次都创建新列表
2. `insert(0, msg)` 是 O(n) 操作
3. 逐条计算效率低

**影响**: 压缩时需要多次遍历消息列表计算 token。

---

### 瓶颈 6：Skills 每次重新加载（⚡ 中优先级）

**位置**: `prompt.py` 第 145-147 行

```python
def build_system_prompt(...):
    ...
    skill_registry = SkillRegistry()  # ← 每次都创建新实例
    if skills_dir:
        skill_registry.load_from_dir(skills_dir)  # ← 每次都重新加载
```

**问题**: 每次构建 system prompt 都重新扫描 skills 目录并加载所有 SKILL.md。

**影响**: 在多次迭代中，skills 目录被反复扫描和解析。

---

## 性能影响链

```
启动时：
Session._load() 
  └── count_tokens(全部消息)  ← tiktoken 逐条 encode
        │
        └── 历史消息越多，启动越慢

运行时（每轮迭代）：
ReactLoop.run()
  ├── compress_if_needed()
  │     ├── build_system_prompt()  ← 文件 I/O + get_schemas() + load_from_dir()
  │     └── count_tokens(全部消息)  ← 重复计算
  │
  └── build_messages("react")
        └── build_system_prompt()  ← 又一次完整构建
```

---

## 优化建议

### 高优先级

1. **启动时跳过精确 token 计算**
   - 改用估算公式：`total_chars × 0.25`（中文字符 ≈ 2 tokens，英文 ≈ 0.25 tokens）
   - 或缓存上次的 token 数，启动时直接读取

2. **缓存 System Prompt**
   - 使用 `@lru_cache` 或手动缓存
   - 文件内容变化时失效

3. **合并 tiktoken encoder 初始化**
   - 提取到单独模块，所有地方引用同一个实例

### 中优先级

4. **优化 compress_if_needed()**
   - 只在添加新消息后检查
   - 增量计算 token 数，而不是每次重算

5. **优化 _pick_recent()**
   - 使用 `deque` 或预计算 token 累积
   - 避免 `insert(0, ...)` 的 O(n) 开销

6. **缓存 SkillRegistry**
   - 使用单例模式
   - 只在 skills 目录变化时重新加载

---

## 测试建议

```python
# 1. 测试启动时间与消息数的关系
import time
for msg_count in [100, 500, 1000, 2000]:
    # 创建测试会话
    session = create_test_session(msg_count)
    
    start = time.time()
    session._load()
    print(f"{msg_count} 条消息: {time.time() - start:.3f}s")

# 2. 测试 build_messages() 缓存效果
ctx = ContextManager(session, workspace)
start = time.time()
for _ in range(100):
    ctx.build_messages("react")
print(f"100 次 build_messages: {time.time() - start:.3f}s")
```

---

## 总结

| 瓶颈 | 优先级 | 影响场景 |
|------|--------|----------|
| 启动时全量 token 计算 | 🔴 高 | 启动 |
| System Prompt 每次重建 | 🔴 高 | 每轮迭代 |
| 压缩阈值检查重复计算 | ⚡ 中 | 每轮迭代 |
| _pick_recent 低效遍历 | ⚡ 中 | 压缩时 |
| Skills 重复加载 | ⚡ 中 | 每轮迭代 |
| tiktoken 重复初始化 | ⚡ 中 | 模块加载 |
