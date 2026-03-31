# QRClaw 上下文管理架构设计

## 一、现状分析

### 1.1 当前上下文流转

```
用户输入 → Router 判断 → Planner 生成计划 → Executor 执行 → 返回结果
                ↓
           ReAct 循环
```

### 1.2 存在的问题

#### 问题 1：Router 重复传 system_prompt

```python
# agent.py
system_prompt = _make_system_prompt(...)
route(user_input, system_prompt=system_prompt["content"], history=session.messages)

# router.py
messages = [{"role": "system", "content": system_prompt}, *history, ...]
# history 里可能已有旧 system 消息，造成重复
```

#### 问题 2：Planner 没有历史上下文

```python
# planner.py
messages = [
    {"role": "system", "content": _PLANNER_SYSTEM},
    {"role": "user", "content": user_input},  # 只有用户输入，没有历史！
]
```

Planner 不知道之前发生过什么，可能生成重复步骤。

#### 问题 3：子 Agent 完全隔离

```python
# agent.py run_sub_agent()
sub_session = Session(...)  # 全新空 session！
result = run(task, sub_session, ...)
return result  # 子 session 被丢弃
```

子 Agent：
- 不知道用户原始需求
- 不知道之前对话内容
- 不知道父 Agent 做了什么
- 不知道兄弟 Agent 做了什么

#### 问题 4：前置步骤结果传递粗糙

```python
# agent.py run_step()
snippet = dep_result[:800] + "\n...(已截断)"  # 截断到 800 字符
task = f"【前置步骤结果】\n{snippet}"
```

- 只传递"结果摘要"，丢失执行过程
- 截断到 800 字符，关键信息可能丢失
- 后续步骤无法追溯前置步骤具体做了什么

#### 问题 5：严重 Bug - results 变量作用域错误

```python
# agent.py _run_with_plan()
def run_step(step, plan_obj):
    dep_result = results.get(dep_id, "")  # ❌ results 此时未定义！
    ...

results = execute_plan(p, console, run_step)  # results 在这里才赋值
```

闭包引用的 `results` 在调用时还未定义，会导致 `NameError`。

---

## 二、需求确认

### 2.1 场景划分

| 场景 | 描述 | 上下文需求 |
|------|------|-----------|
| 主 Agent 单轮 | 用户提问 → 直接回答 | 无特殊需求 |
| 主 Agent 多轮 | 连续对话，需要记住之前的内容 | 需要 WorkingMemory |
| 串行 Plan | Step 1 → Step 2 → Step 3 | Step 2 需要知道 Step 1 的结果 |
| 并行 Plan | Step 1/2/3 同时执行 | 不需要共享，各自独立 |

### 2.2 核心需求

1. **WorkingMemory**：存储当前任务关键信息
   - 任务目标
   - 相关文件
   - 关键发现
   - 已做决策
   - 产出文件

2. **StepResult**：存储每个步骤的执行结果
   - 摘要（给后续步骤看）
   - 完整消息历史（可追溯）
   - 产出文件

3. **串行步骤继承上下文**：有依赖的步骤需要知道前置步骤的结果

4. **并行步骤独立执行**：同层步骤互不干扰

5. **并行后整合**：并行步骤结束后，由主 Agent 整合 WorkingMemory

---

## 三、方案设计

### 3.1 数据结构

#### WorkingMemory

```python
@dataclass
class WorkingMemory:
    """当前任务的工作记忆，会话级别，跨步骤共享"""
    
    # 任务信息
    goal: str = ""                      # 当前任务目标
    original_request: str = ""          # 用户原始请求
    
    # 收集的信息
    relevant_files: list[str]           # 发现的相关文件
    key_findings: list[str]             # 关键发现
    decisions: list[str]                # 做出的决策
    
    # 产出物
    created_files: list[str]            # 创建的文件
    modified_files: list[str]           # 修改的文件
    
    # 方法
    def to_prompt(self) -> str          # 转成 prompt 字符串
    def merge(self, other)              # 合并（用于并行后整合）
    def copy(self)                      # 深拷贝（用于子 Agent 继承）
```

#### StepResult

```python
@dataclass
class StepResult:
    """单个步骤的执行结果"""
    
    # 基本信息
    step_id: int
    description: str
    status: str  # "success" | "failed"
    
    # 结果
    summary: str                        # 简短摘要（给后续步骤看）
    output: str                         # 子 Agent 的最终输出
    messages: list[dict]                # 完整消息历史（可追溯）
    
    # 产出物
    created_files: list[str]
    modified_files: list[str]
    
    # 方法
    def to_context_prompt(self) -> str  # 给后续步骤看的上下文
```

#### Session 扩展

```python
class Session:
    # 现有字段
    session_id: str
    messages: list[dict]
    
    # 新增字段
    working_memory: WorkingMemory       # 工作记忆
    step_results: dict[int, StepResult] # 步骤结果 {step_id: StepResult}
```

---

### 3.2 上下文流转

#### 主 Agent 流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                        主 Session                                   │
│                                                                      │
│  messages: [user1, assistant1, tool1, ...]                         │
│  working_memory: {goal, relevant_files, key_findings, ...}         │
│  step_results: {}                                                   │
└─────────────────────────────────────────────────────────────────────┘
         │
         │ 用户输入
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Router 判断                                   │
│                                                                      │
│  messages = [                                                        │
│      {"role": "system", "content": 静态 prompt},                    │
│      {"role": "system", "content": working_memory.to_prompt()},     │
│      *session.messages,                                              │
│  ]                                                                   │
└─────────────────────────────────────────────────────────────────────┘
         │
    ┌────┴────┐
    ▼         ▼
 direct     plan
    │         │
    │         ▼
    │    ┌─────────────────────────────────────────────────────────────┐
    │    │  Planner（有历史上下文）                                     │
    │    │                                                            │
    │    │  messages = [                                              │
    │    │      {"role": "system", "content": PLANNER_SYSTEM},        │
    │    │      {"role": "system", "content": 工作记忆 + 历史摘要},    │
    │    │      {"role": "user", "content": user_input},              │
    │    │  ]                                                         │
    │    └─────────────────────────────────────────────────────────────┘
    │         │
    │         ▼
    │    ┌─────────────────────────────────────────────────────────────┐
    │    │  Executor 执行                                              │
    │    │                                                            │
    │    │  根据依赖关系分层执行：                                      │
    │    │  - 并行层：各自独立，继承 working_memory 快照（只读）        │
    │    │  - 串行层：继承 working_memory + 注入前置步骤结果            │
    │    └─────────────────────────────────────────────────────────────┘
    │
    ▼
ReAct 循环
```

#### 并行步骤执行

```
         Step 1 ──────────────────┐
         Step 2 ──────────────────┤ (同层并行)
         Step 3 ──────────────────┘
                    │
                    ▼
              Step 4 (依赖 1,2,3)

执行过程：

T1: 启动 Step 1, 2, 3 的子 agent（并行）
    │
    ├─ Thread-1: sub_session_1
    │             working_memory = parent.working_memory.copy()  # 快照
    │             # 各自独立执行，不修改 parent.working_memory
    │
    ├─ Thread-2: sub_session_2
    │             working_memory = parent.working_memory.copy()  # 快照
    │
    └─ Thread-3: sub_session_3
                  working_memory = parent.working_memory.copy()  # 快照
    │
T2: 等待 Step 1, 2, 3 完成
    │
    │  # 保存结果到 parent.step_results（加锁）
    │  parent.step_results[1] = StepResult(...)
    │  parent.step_results[2] = StepResult(...)
    │  parent.step_results[3] = StepResult(...)
    │
    │  # 整合 working_memory（主 Agent 负责）
    │  parent.working_memory.merge(sub_session_1.working_memory)
    │  parent.working_memory.merge(sub_session_2.working_memory)
    │  parent.working_memory.merge(sub_session_3.working_memory)
    │
T3: 启动 Step 4 的子 agent
    │
    └─ Thread-4: sub_session_4
                 working_memory = parent.working_memory  # 继承整合后的
                 
                 # 注入前置步骤结果
                 session.add({
                     "role": "user",
                     "content": StepResult[1].to_context_prompt()
                                 + StepResult[2].to_context_prompt()
                                 + StepResult[3].to_context_prompt()
                 })
```

---

### 3.3 核心代码逻辑

#### 串行步骤执行

```python
def run_step(step, plan_obj, parent_session: Session) -> str:
    """执行单个步骤（串行场景）"""
    
    # 1. 创建子 session
    sub_session = Session(
        sessions_dir=parent_session.sessions_dir,
        session_id=f"{parent_session.session_id}-step-{step.id}",
        resume=False,
    )
    
    # 2. 继承 working_memory
    sub_session.working_memory = parent_session.working_memory.copy()
    
    # 3. 注入前置步骤结果
    if step.depends_on:
        context_parts = ["## 前置步骤结果\n"]
        for dep_id in step.depends_on:
            if dep_id in parent_session.step_results:
                dep_result = parent_session.step_results[dep_id]
                context_parts.append(dep_result.to_context_prompt())
                context_parts.append("")
        
        sub_session.add({
            "role": "user",
            "content": "\n".join(context_parts),
        })
    
    # 4. 构建任务
    task = f"【计划目标】{plan_obj.goal}\n【当前步骤】Step {step.id}: {step.description}"
    sub_session.add({"role": "user", "content": task})
    
    # 5. 执行子 agent
    result = run(task, sub_session, ...)
    
    # 6. 保存结果
    step_result = create_step_result(
        step_id=step.id,
        description=step.description,
        messages=sub_session.messages,
        output=result,
    )
    parent_session.step_results[step.id] = step_result
    
    # 7. 同步 working_memory
    parent_session.working_memory = sub_session.working_memory
    
    # 8. 持久化子 session
    sub_session.save()
    
    return result
```

#### 并行步骤执行

```python
def execute_plan(plan, console, run_step_fn, parent_session: Session):
    """执行计划，并行步骤不共享，串行步骤继承上下文"""
    
    layers = _topological_layers(plan.steps)
    results_lock = threading.Lock()
    
    for layer in layers:
        if len(layer) == 1:
            # 串行：继承上下文，同步 working_memory
            step = layer[0]
            result = run_step_fn(step, plan, parent_session)
        
        else:
            # 并行：各自独立，只读 working_memory
            threads = []
            
            def _run_parallel(step):
                # 创建子 session
                sub_session = Session(...)
                
                # 继承 working_memory 快照（只读）
                sub_session.working_memory = parent_session.working_memory.copy()
                
                # 执行
                result = run(task, sub_session, ...)
                
                # 保存结果（加锁）
                with results_lock:
                    parent_session.step_results[step.id] = StepResult(...)
                
                # 返回子 session 的 working_memory（用于整合）
                return sub_session.working_memory
            
            parallel_results = []
            for step in layer:
                t = threading.Thread(
                    target=lambda s: parallel_results.append((s.id, _run_parallel(s))),
                    args=(step,),
                )
                threads.append(t)
                t.start()
            
            for t in threads:
                t.join()
            
            # 整合 working_memory（方案 C）
            for step_id, wm in parallel_results:
                parent_session.working_memory.merge(wm)
```

---

## 四、文件改动清单

```
qrclaw/
├── memory/
│   ├── __init__.py              # 导出新类
│   ├── session.py               # 扩展：加 working_memory 和 step_results
│   ├── working_memory.py        # 新增：WorkingMemory 类 ✅ 已完成
│   └── step_result.py           # 新增：StepResult 类
│
├── agent.py                     # 改动：
│                                #   - run_step 传递 parent_session
│                                #   - 子 agent 继承 working_memory
│                                #   - 串行时注入前置结果
│                                #   - 修复 results 变量作用域 bug
│
└── graph/
    └── executor.py              # 改动：
                                 #   - 接收 parent_session
                                 #   - 并行时线程安全处理
                                 #   - 并行后整合 working_memory
```

---

## 五、待实现项

### 5.1 已完成

- [x] WorkingMemory 类

### 5.2 待完成

- [ ] StepResult 类
- [ ] Session 扩展
- [ ] executor.py 改造
- [ ] agent.py 改造
- [ ] 修复 results 变量作用域 bug
- [ ] 测试验证

---

## 六、后续优化方向

1. **WorkingMemory 持久化**：考虑是否需要跨会话保留
2. **StepResult 压缩**：messages 可能很大，考虑压缩存储
3. **智能继承**：子 Agent 只继承相关的历史消息，而非全部
4. **工具集成**：让 Agent 通过工具主动更新 WorkingMemory

---

**文档版本**: v1.0  
**创建时间**: 2026-03-27  
**作者**: QRClaw