# Claude Code 持久化记忆分层架构分析报告

## 一、目录结构概览

```
src/
├── memdir/                        # 记忆核心模块
│   ├── memdir.ts                  # 主入口：加载/构建记忆提示词
│   ├── memoryTypes.ts             # 记忆类型定义 (user/feedback/project/reference)
│   ├── memoryScan.ts              # 扫描记忆文件元数据
│   ├── findRelevantMemories.ts    # LLM 智能检索相关记忆
│   ├── paths.ts                   # 路径解析 & 启用状态检查
│   ├── teamMemPaths.ts            # 团队记忆路径 & 安全验证
│   └── memoryAge.ts               # 记忆老化相关
│
├── tools/AgentTool/
│   ├── agentMemory.ts             # Agent 记忆管理 (user/project/local scope)
│   └── agentMemorySnapshot.ts     # Agent 记忆快照同步
```

---

## 二、持久化记忆分层架构

### 2.1 三层记忆系统

| 层级 | 名称 | 路径模式 | Scope | 同步方式 |
|------|------|----------|-------|----------|
| **L1** | Auto Memory | `<memoryBase>/projects/<sanitized-root>/memory/` | user (全局) | 本地持久化 |
| **L2** | Agent Memory | `~/.claude/agent-memory/<agentType>/` | user/project/local | VCS 共享 (project) |
| **L3** | Team Memory | `<autoMem>/team/` | team (子目录) | 共享目录 |

### 2.2 路径解析优先级 (Auto Memory)

```
1. CLAUDE_COWORK_MEMORY_PATH_OVERRIDE  (env var, Cowork SDK 使用)
2. autoMemoryDirectory in settings.json (userSettings/localSettings)
3. <memoryBase>/projects/<sanitized-git-root>/memory/
   where memoryBase = CLAUDE_CODE_REMOTE_MEMORY_DIR ?? ~/.claude
```

### 2.3 Agent Memory Scope 路径

```typescript
type AgentMemoryScope = 'user' | 'project' | 'local'

// user:   <memoryBase>/agent-memory/<agentType>/
// project: <cwd>/.claude/agent-memory/<agentType>/
// local:  <cwd>/.claude/agent-memory-local/<agentType>/
//         或 CLAUDE_CODE_REMOTE_MEMORY_DIR/projects/<git-root>/agent-memory-local/
```

---

## 三、预防记忆过多的核心策略

### 3.1 MEMORY.md 入口点双限制 (memdir.ts)

```typescript
export const MAX_ENTRYPOINT_LINES = 200   // 行数硬限制
export const MAX_ENTRYPOINT_BYTES = 25_000 // 字节硬限制 (~125 chars/line)
```

**截断流程**：
1. 先按行数截断 (取前 200 行)
2. 再按字节数截断 (在最后一个换行符处切断，防止截断 mid-line)
3. 附加警告信息，说明哪个限制触发了

### 3.2 记忆文件数量上限 (memoryScan.ts)

```typescript
const MAX_MEMORY_FILES = 200   // scanMemoryFiles 返回上限
const FRONTMATTER_MAX_LINES = 30 // 只读取前 30 行获取元数据
```

### 3.3 语义去重检查

**保存前检查**：
```
- Do not write duplicate memories. First check if there is an existing 
  memory you can update before writing a new one.
```

### 3.4 LLM 智能相关性检索 (findRelevantMemories.ts)

```typescript
// 查询时只返回最多 5 个相关记忆
const selectedFilenames = await selectRelevantMemories(query, memories)
// 输出格式: { selected_memories: string[] }  (max 5 items)
```

**选择提示词关键约束**：
- Only include memories that you are certain will be helpful
- If you are unsure, do not include it in your list
- If no memories are clearly useful, return empty list

### 3.5 双 Agent 协作模式

```
Main Agent (前台)
  ├── 直接保存记忆
  └── hasMemoryWritesSince() 标记写入范围

Extract Agent (后台, fork)
  └── 扫描并补充 Main Agent 遗漏的记忆
```

---

## 四、记忆类型分类与约束

### 4.1 四种记忆类型 (memoryTypes.ts)

| 类型 | 描述 | 适用范围 |
|------|------|----------|
| **user** | 用户角色、目标、知识 | user |
| **feedback** | 指导/纠正/成功确认 | user 或 team (默认 private) |
| **project** | 项目上下文、目标、deadlines | private 或 team (倾向 team) |
| **reference** | 外部系统指针 (Linear, Grafana 等) | 通常 team |

### 4.2 frontmatter 格式

```yaml
---
name: {{memory name}}
description: {{one-line description — used for relevance matching}}
type: {{user, feedback, project, reference}}
---

{{content}}
```

### 4.3 明确排除的内容

```
- 代码模式、架构、文件路径 (可从代码派生)
- Git 历史、recent changes
- 调试解决方案 (修复在代码里)
- CLAUDE.md 已记录的内容
- 临时任务详情
```

---

## 五、安全防护机制

### 5.1 路径遍历防护 (teamMemPaths.ts)

```typescript
// 第一层: resolve() 规范化 .. 段
// 第二层: realpathDeepestExisting() 解析符号链接
// 第三层: isRealPathWithinTeamDir() 验证真实路径包含
```

**检测的攻击向量**：
- `../` 路径穿越
- URL 编码穿越 (`%2e%2e%2f`)
- Unicode 范化攻击 (`００../` → `../`)
- Windows 反斜杠穿越
- 悬空符号链接
- 符号链接循环

### 5.2 路径验证函数

```typescript
isAgentMemoryPath(absolutePath: string): boolean
isAutoMemPath(absolutePath: string): boolean
isTeamMemPath(filePath: string): boolean
validateTeamMemWritePath(filePath: string): Promise<string>
validateTeamMemKey(relativeKey: string): Promise<string>
```

---

## 六、快照同步机制 (agentMemorySnapshot.ts)

### 6.1 快照检查流程

```typescript
checkAgentMemorySnapshot(agentType, scope)
// 返回: { action: 'none' | 'initialize' | 'prompt-update' }
```

### 6.2 同步元数据

```json
// .snapshot-synced.json
{ "syncedFrom": "<snapshot-timestamp>" }
```

### 6.3 三种同步动作

| 动作 | 场景 |
|------|------|
| `none` | 无快照或已同步 |
| `initialize` | 本地无记忆，从快照初始化 |
| `prompt-update` | 快照更新了，替换本地记忆 |

---

## 七、关键常量汇总

| 常量 | 值 | 文件 |
|------|-----|------|
| MAX_ENTRYPOINT_LINES | 200 | memdir.ts |
| MAX_ENTRYPOINT_BYTES | 25,000 | memdir.ts |
| MAX_MEMORY_FILES | 200 | memoryScan.ts |
| FRONTMATTER_MAX_LINES | 30 | memoryScan.ts |
| SELECT_MAX_MEMORIES | 5 | findRelevantMemories.ts |

---

## 八、与 QRClaw 的对比洞察

### 8.1 QRClaw 当前实现
- 简单的中期记忆 (in-memory Map)
- 无持久化到磁盘
- 无分层架构

### 8.2 Claude Code 优秀实践
1. **入口点截断**: 双限制 (行+字节) 防止上下文爆炸
2. **智能检索**: LLM 选择最相关的 5 个记忆
3. **类型分类**: 四种类型 + frontmatter 元数据
4. **双 Agent 协作**: 前台保存 + 后台补充
5. **安全验证**: 多层路径验证 + symlink 检测
6. **快照同步**: 支持 VCS 共享和远程挂载

### 8.3 可借鉴的优化方向
- 实现 MEMORY.md 入口点 + 截断机制
- 添加记忆文件数量上限
- 引入 LLM 驱动的相关性检索
- 添加 frontmatter 元数据支持
- 实现快照同步机制

---

*分析完成时间: $(date '+%Y-%m-%d %H:%M:%S')*
*数据来源: Claude Code v1.x src/memdir/*, src/tools/AgentTool/*
