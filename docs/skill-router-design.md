# 技能路由器设计（守门员机制）

> 创建时间：2025-03-29
> 
> 核心问题：LLM 调用工具是直接执行的，中间没有判断层

---

## 一、问题背景

### 1.1 现有流程

```
用户输入
    ↓
LLM 推理
    ↓
LLM 返回 tool_calls
    ↓
系统直接执行 execute(name, arguments)  ← 没有守门员
    ↓
返回结果
```

### 1.2 问题

```
问题1：LLM 可能误判意图
- 用户说"这个发票能报销吗？"
- LLM 以为是走报销流程，开始收集发票
- 但用户其实只是想咨询

问题2：safe 模式的 skill 需要确认
- 报销、审批等敏感操作
- 不应该由 LLM 单独决定执行
- 需要有"守门员"把关

问题3：流程中断后如何恢复
- 用户在流程中突然插话
- 需要保存状态、恢复状态
```

---

## 二、解决方案：守门员机制

### 2.1 核心思路

```
LLM 返回 tool_calls
    ↓
┌─────────────────────┐
│ 守门员判断           │  ← 新增
│ should_execute()?    │
└─────────────────────┘
    ↓
  通过？
    ├─ 是 → execute() → 返回结果
    └─ 否 → 返回拒绝理由 → LLM 继续对话
```

### 2.2 判断流程（两阶段判断）

```
第一层：规则快速判断（确定性高）
    ├─ 包含关键词"报销/申请/办理" → 通过
    ├─ 包含关键词"是多少/什么是/咨询" → 拒绝
    └─ 无法判断 → 进入第二层

第二层：LLM 意图识别（模糊情况）
    ├─ 输入：JSON + 对话上下文
    └─ 输出：执行/拒绝 + 理由

第三层：用户确认（高风险操作）
    └─ 敏感操作需要用户手动确认
```

---

## 三、设计细节

### 3.1 技能配置

```yaml
# skills/finance/reimbursement.yaml
name: 报销处理
mode: safe              # safe=需要守门员，free=直接执行
risk: medium            # low/medium/high
triggers:
  - 报销
  - 申请报销
  - 提交报销
  - 办理报销
description: 处理员工报销申请
steps:
  - id: 1
    name: 收集发票
    action: ask_user
    ...
```

```yaml
# skills/finance/consultation.yaml
name: 财务咨询
mode: free              # free 模式，直接执行
triggers:
  - 咨询
  - 问一下
description: 回答财务相关问题
prompt: |
  你是公司财务助手...
```

### 3.2 守门员实现

```python
# qrclaw/skill_router.py

class SkillRouter:
    """技能路由器 - 守门员"""
    
    def __init__(self):
        # 规则关键词
        self.action_keywords = ["报销", "申请", "办理", "提交", "流程", "处理"]
        self.question_keywords = ["是多少", "什么是", "怎么算", "为什么", "咨询", "问一下"]
    
    def should_execute(self, skill_name: str, arguments: dict, context: dict) -> tuple[bool, str]:
        """
        判断是否应该执行这个 skill
        
        返回：(是否执行, 原因)
        """
        
        # 1. 获取 skill 配置
        skill_config = SKILL_CONFIG.get(skill_name, {})
        mode = skill_config.get("mode", "free")
        
        # 2. free 模式，直接通过
        if mode == "free":
            return True, "free模式，直接执行"
        
        # 3. safe 模式，需要判断
        user_input = context.get("user_input", "")
        history = context.get("history", [])
        
        # 3.1 规则判断 - 用户明确说要执行
        if any(kw in user_input for kw in self.action_keywords):
            return True, "用户明确要执行流程"
        
        # 3.2 规则判断 - 用户在问问题
        if any(kw in user_input for kw in self.question_keywords):
            return False, "用户在咨询问题，建议继续对话"
        
        # 3.3 上下文判断 - 上一轮在执行流程，继续
        if history and history[-1].get("in_workflow"):
            return True, "继续执行流程"
        
        # 3.4 规则判断不了，问用户
        return None, "无法确定用户意图，请用户确认"
    
    def ask_user_confirm(self, skill_name: str) -> bool:
        """询问用户确认"""
        skill_config = SKILL_CONFIG.get(skill_name, {})
        skill_display_name = skill_config.get("name", skill_name)
        
        console.print(f"[bold yellow]检测到您可能要执行【{skill_display_name}】，确认吗？[/bold yellow]")
        console.print(f"  y - 确认执行")
        console.print(f"  n - 取消，继续对话")
        
        choice = input().strip().lower()
        return choice == "y"
```

### 3.3 Agent 修改

```python
# qrclaw/agent.py

from qrclaw.skill_router import SkillRouter

skill_router = SkillRouter()

def run(user_input: str, session: Session, ...):
    # ... 现有代码 ...
    
    for tc in response.tool_calls:
        name = tc.name
        arguments = tc.arguments
        
        # === 守门员判断 ===
        context = {
            "user_input": user_input,
            "history": session.messages,
        }
        should_execute, reason = skill_router.should_execute(name, arguments, context)
        
        # 规则判断不了，问用户
        if should_execute is None:
            should_execute = skill_router.ask_user_confirm(name)
            reason = "用户确认执行" if should_execute else "用户取消执行"
        
        if not should_execute:
            # 拒绝执行
            console.print(f"[bold yellow]守门员：{reason}[/bold yellow]")
            
            session.add({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": f"建议：{reason}。请继续与用户对话。",
            })
            continue
        
        # === 执行工具 ===
        result = execute(name, arguments)
        session.add({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result,
        })
```

---

## 四、完整执行流程

```
用户输入
    ↓
LLM 推理
    ↓
LLM 返回 tool_calls
    ↓
┌─────────────────────────────┐
│ 守门员判断                   │
│                             │
│ 1. 获取 skill 配置           │
│    - mode = free → 直接通过  │
│    - mode = safe → 需要判断  │
│                             │
│ 2. 规则判断（safe 模式）      │
│    - action_keywords → 通过  │
│    - question_keywords → 拒绝│
│    - 无法判断 → 问用户        │
│                             │
│ 3. 用户确认（可选）           │
│    - 高风险操作需要确认       │
└─────────────────────────────┘
    ↓
  通过？
    ├─ 是 → execute() → 返回结果
    └─ 否 → 返回拒绝理由 → LLM 继续对话
```

---

## 五、拒绝后的处理

### 5.1 拒绝时返回给 LLM 的内容

```python
# 拒绝时，返回明确的建议

session.add({
    "role": "tool",
    "tool_call_id": tc.id,
    "content": "建议：用户可能在咨询问题，请继续与用户对话。",
})
```

### 5.2 LLM 的调整

```
LLM 收到拒绝理由后：

场景1：用户在问问题
- LLM 收到："建议：用户在咨询问题，请继续对话"
- LLM 调整：改成回答问题，而不是执行流程

场景2：意图不明确
- LLM 收到："无法确定用户意图"
- LLM 调整：主动问用户"您是想执行XX流程，还是咨询问题？"

场景3：用户取消
- LLM 收到："用户取消执行"
- LLM 调整：继续对话，不执行流程
```

---

## 六、风险等级控制

### 6.1 风险等级定义

```python
SKILL_CONFIG = {
    "报销处理": {
        "mode": "safe",
        "risk": "medium",
        "need_confirm": False,     # 不需要用户确认
    },
    "删除数据": {
        "mode": "safe", 
        "risk": "high",
        "need_confirm": True,      # 需要用户确认
    },
    "财务咨询": {
        "mode": "free",
        "risk": "low",
        "need_confirm": False,
    },
}
```

### 6.2 执行策略

```python
def should_execute(self, skill_name: str, ...):
    skill_config = SKILL_CONFIG.get(skill_name, {})
    risk = skill_config.get("risk", "low")
    
    # 规则判断
    should_execute, reason = self.rule_check(...)
    
    if should_execute:
        # 高风险操作，需要用户确认
        if risk == "high":
            user_confirm = self.ask_user_confirm(skill_name)
            if not user_confirm:
                return False, "用户取消执行"
    
    return should_execute, reason
```

---

## 七、流程中断与恢复

### 7.1 问题场景

```
用户：我要报销
系统：启动报销流程...步骤1：请上传发票
用户：对了，差旅标准是多少？（插话）
系统：？？？
```

### 7.2 解决方案

```python
class Session:
    def __init__(self):
        self.mode = "chat"           # 当前模式
        self.workflow_state = None   # 流程状态（中断时保存）
    
    def interrupt_workflow(self):
        """中断流程，保存状态"""
        self.workflow_state = {
            "workflow_id": self.current_workflow,
            "current_step": self.current_step,
            "context": self.context,
            "timestamp": now()
        }
        self.mode = "chat"
    
    def resume_workflow(self):
        """恢复流程"""
        if self.workflow_state:
            self.current_workflow = self.workflow_state["workflow_id"]
            self.current_step = self.workflow_state["current_step"]
            self.context = self.workflow_state["context"]
            self.mode = "workflow"
            self.workflow_state = None  # 清空中断状态
```

### 7.3 执行流程

```
用户：我要报销
系统：启动报销流程（mode=workflow）
      步骤1：请上传发票
      [状态保存：workflow_id, current_step=1]

用户：对了，差旅标准是多少？（插话）
系统：检测到插话，中断流程
      [保存流程状态]
      切换到聊天模式（mode=chat）
      回答：差旅标准是每天 300 元...
      提示：您有未完成的报销流程，要继续吗？

用户：好的，继续
系统：恢复流程（mode=workflow）
      [从保存的状态恢复]
      步骤1：请上传发票
```

---

## 八、关键技术点

### 8.1 规则关键词

```python
# 动作关键词（倾向执行流程）
action_keywords = [
    "报销", "申请", "办理", "提交", "流程", "处理",
    "我要", "帮我", "开始"
]

# 问题关键词（倾向继续聊天）
question_keywords = [
    "是多少", "什么是", "怎么算", "为什么", "咨询", "问一下",
    "多少", "怎么", "为什么", "吗", "？"
]
```

### 8.2 上下文判断

```python
# 上一轮在执行流程，这轮继续
if history and history[-1].get("in_workflow"):
    return True, "继续执行流程"

# 用户明确说"取消/退出"
if "取消" in user_input or "退出" in user_input:
    return False, "用户取消流程"
```

### 8.3 置信度

```python
# 规则判断的置信度
if has_action_keyword:
    return True, "置信度: 高（明确动作词）"

if has_question_keyword:
    return False, "置信度: 高（明确问题词）"

# 置信度低，问用户
return None, "置信度: 低，需要用户确认"
```

---

## 九、与双模式设计的关系

### 9.1 配合使用

```
技能路由器（守门员）+ Agent 双模式设计

Agent（财务）
    │
    ├── 技能1：报销处理
    │       └── mode: safe → 需要守门员判断
    │
    ├── 技能2：财务咨询
    │       └── mode: free → 直接执行，不经过守门员
    │
    └── 技能3：预算查询
            └── mode: safe → 需要守门员判断
```

### 9.2 执行流程

```
LLM 准备调用 skill
    ↓
检查 skill 的 mode
    ├─ free → 直接执行
    └─ safe → 守门员判断
                 ├─ 通过 → 执行
                 └─ 拒绝 → 返回理由，LLM 继续对话
```

---

## 十、实现步骤

### 第一步：实现守门员基础版

```python
# 只做规则判断
# 不做 LLM 判断，不确定就问用户
```

### 第二步：加入技能配置

```python
# 定义 skill 时标记 mode 和 risk
# 守门员根据配置决定判断策略
```

### 第三步：实现流程中断恢复

```python
# Session 中保存流程状态
# 插话时中断，用户要求时恢复
```

### 第四步：优化 LLM 判断（可选）

```python
# 规则判断不了时，用 LLM 判断
# 返回置信度，低置信度问用户
```

---

## 十一、总结

### 核心价值

```
1. 守门员机制
   - LLM 想调用工具，必须先过守门员
   - 防止 LLM 误判意图

2. 两阶段判断
   - 规则优先（确定性高）
   - LLM 补充（模糊情况）
   - 用户确认（高风险）

3. 拒绝可恢复
   - 拒绝不是失败
   - 告诉 LLM 原因
   - LLM 可以调整策略
```

### 一句话

> LLM 不确定性是客观存在的，解决思路不是消除不确定性，而是：
> 1. 规则优先，减少 LLM 判断
> 2. 置信度低就问用户
> 3. 拒绝后给 LLM 反馈，让它调整策略

---

*文档整理自 QRClaw 项目讨论*