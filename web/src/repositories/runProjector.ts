import type { EventEnvelopeDto } from '../models/protocol'
import type { Agent, Approval, Plan, RunSnapshot, ToolCall } from '../models/workbench'

/** 从事件数据中安全读取字符串。 */
function text(data: Record<string, unknown>, key: string, fallback = ''): string {
  const value = data[key]
  return typeof value === 'string' ? value : fallback
}

/** 从事件数据中安全读取对象。 */
function record(data: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = data[key]
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 把 plan.created / plan.updated 的协议数据转换为领域计划。 */
function mapEventPlan(data: Record<string, unknown>): Plan {
  const steps = Array.isArray(data.steps) ? data.steps : []
  return {
    id: text(data, 'plan_id'),
    goal: text(data, 'goal'),
    projectPath: text(data, 'project_path'),
    revision: typeof data.revision === 'number' ? data.revision : 1,
    steps: steps.map((raw) => {
      const step = raw as Record<string, unknown>
      return {
        id: text(step, 'step_id'),
        description: text(step, 'description'),
        dependsOn: Array.isArray(step.depends_on)
          ? step.depends_on.filter((item): item is string => typeof item === 'string')
          : [],
        status: 'pending' as const,
        output: null,
      }
    }),
  }
}

/** 复制快照，防止 React 状态被原地修改。 */
function copySnapshot(snapshot: RunSnapshot): RunSnapshot {
  return {
    ...snapshot,
    run: { ...snapshot.run },
    plan: snapshot.plan
      ? { ...snapshot.plan, steps: snapshot.plan.steps.map((step) => ({ ...step })) }
      : null,
    agents: snapshot.agents.map((agent) => ({ ...agent })),
    toolCalls: snapshot.toolCalls.map((tool) => ({ ...tool })),
    pendingApprovals: snapshot.pendingApprovals.map((approval) => ({ ...approval })),
    messages: snapshot.messages.map((message) => ({ ...message })),
    fileChanges: [...snapshot.fileChanges],
    usage: { ...snapshot.usage },
  }
}

/**
 * 将一条后端事件幂等归并到领域快照。
 * 逻辑与后端 RunProjector 保持一致，状态事实仍以后端事件为准。
 */
export function applyRunEvent(current: RunSnapshot, event: EventEnvelopeDto): RunSnapshot {
  if (event.seq <= current.run.lastSeq) return current
  const snapshot = copySnapshot(current)
  snapshot.run.lastSeq = event.seq
  snapshot.run.updatedAt = event.timestamp
  const data = event.data
  const agent = snapshot.agents.find((item) => item.id === event.agent_id)

  switch (event.type) {
    case 'run.started':
      snapshot.run.status = 'running'
      break
    case 'run.status_changed': {
      const status = text(data, 'status')
      if (status) snapshot.run.status = status as typeof snapshot.run.status
      break
    }
    case 'route.decided':
      snapshot.run.route = text(data, 'route')
      break
    case 'plan.created':
      snapshot.plan = mapEventPlan(data)
      break
    case 'plan.updated': {
      const updated = mapEventPlan(data)
      if (snapshot.plan) {
        for (const step of updated.steps) {
          const previous = snapshot.plan.steps.find((item) => item.id === step.id)
          if (previous) {
            step.status = previous.status
            step.output = previous.output
          }
        }
      }
      snapshot.plan = updated
      break
    }
    case 'step.started':
    case 'step.completed':
    case 'step.failed': {
      const step = snapshot.plan?.steps.find((item) => item.id === text(data, 'step_id'))
      if (step) {
        step.status =
          event.type === 'step.started'
            ? 'running'
            : event.type === 'step.completed'
              ? 'completed'
              : 'failed'
        step.output = text(data, event.type === 'step.failed' ? 'error' : 'output') || step.output
      }
      break
    }
    case 'agent.started': {
      if (agent) {
        agent.status = 'running'
        agent.startedAt = event.timestamp
      } else {
        const created: Agent = {
          id: event.agent_id ?? '',
          parentAgentId: event.parent_agent_id,
          runId: event.run_id,
          stepId: text(data, 'step_id') || null,
          name: text(data, 'name', 'Agent'),
          task: text(data, 'task'),
          status: 'running',
          currentAction: '',
          decisionSummary: '',
          startedAt: event.timestamp,
          completedAt: null,
          result: null,
          error: null,
        }
        snapshot.agents.push(created)
      }
      break
    }
    case 'agent.progress':
      if (agent) {
        if (typeof data.current_action === 'string') agent.currentAction = data.current_action
        if (typeof data.decision_summary === 'string') agent.decisionSummary = data.decision_summary
      }
      break
    case 'agent.completed':
    case 'agent.failed':
    case 'agent.cancelled':
      if (agent) {
        agent.status =
          event.type === 'agent.completed'
            ? 'completed'
            : event.type === 'agent.failed'
              ? 'failed'
              : 'cancelled'
        agent.completedAt = event.timestamp
        agent.result = text(data, 'result') || null
        agent.error = text(data, 'error') || null
      }
      break
    case 'tool.approval_required': {
      const toolCallId = text(data, 'tool_call_id')
      let tool = snapshot.toolCalls.find((item) => item.id === toolCallId)
      if (!tool) {
        tool = {
          id: toolCallId,
          runId: event.run_id,
          agentId: event.agent_id ?? '',
          name: text(data, 'tool_name'),
          arguments: record(data, 'arguments'),
          status: 'waiting_approval',
          startedAt: null,
          completedAt: null,
          result: null,
          error: null,
        }
        snapshot.toolCalls.push(tool)
      } else {
        tool.status = 'waiting_approval'
      }
      const approvalId = text(data, 'approval_id')
      if (!snapshot.pendingApprovals.some((item) => item.id === approvalId)) {
        const approval: Approval = {
          id: approvalId,
          toolCallId,
          runId: event.run_id,
          agentId: event.agent_id ?? '',
          createdAt: event.timestamp,
          details: data,
        }
        snapshot.pendingApprovals.push(approval)
      }
      if (agent) agent.status = 'waiting_approval'
      break
    }
    case 'tool.approval_resolved': {
      const approvalId = text(data, 'approval_id')
      snapshot.pendingApprovals = snapshot.pendingApprovals.filter(
        (item) => item.id !== approvalId,
      )
      if (agent?.status === 'waiting_approval') agent.status = 'running'
      break
    }
    case 'tool.started': {
      const toolCallId = text(data, 'tool_call_id')
      let tool = snapshot.toolCalls.find((item) => item.id === toolCallId)
      if (!tool) {
        const created: ToolCall = {
          id: toolCallId,
          runId: event.run_id,
          agentId: event.agent_id ?? '',
          name: text(data, 'name'),
          arguments: record(data, 'arguments'),
          status: 'running',
          startedAt: event.timestamp,
          completedAt: null,
          result: null,
          error: null,
        }
        snapshot.toolCalls.push(created)
      } else {
        tool.status = 'running'
        tool.startedAt = event.timestamp
      }
      break
    }
    case 'tool.completed':
    case 'tool.failed':
    case 'tool.denied': {
      const tool = snapshot.toolCalls.find((item) => item.id === text(data, 'tool_call_id'))
      if (tool) {
        tool.status =
          event.type === 'tool.completed'
            ? 'completed'
            : event.type === 'tool.failed'
              ? 'failed'
              : 'denied'
        tool.completedAt = event.timestamp
        tool.result = text(data, 'result') || null
        tool.error = text(data, 'error') || null
      }
      break
    }
    case 'assistant.delta': {
      const messageId = text(data, 'message_id')
      let message = snapshot.messages.find((item) => item.id === messageId)
      if (!message) {
        message = { id: messageId, content: '', completed: false }
        snapshot.messages.push(message)
      }
      message.content += text(data, 'delta')
      break
    }
    case 'assistant.completed': {
      const messageId = text(data, 'message_id')
      let message = snapshot.messages.find((item) => item.id === messageId)
      if (!message) {
        message = { id: messageId, content: text(data, 'content'), completed: true }
        snapshot.messages.push(message)
      } else {
        message.completed = true
        if (!message.content) message.content = text(data, 'content')
      }
      break
    }
    case 'file.changed':
      snapshot.fileChanges.push(data)
      break
    case 'usage.updated':
      snapshot.usage = { ...snapshot.usage, ...data }
      break
    case 'run.completed':
      snapshot.run.status = 'completed'
      break
    case 'run.failed':
      snapshot.run.status = 'failed'
      snapshot.run.error = data
      finishOpenItems(snapshot, event.timestamp, 'failed')
      break
    case 'run.cancelled':
      snapshot.run.status = 'cancelled'
      snapshot.pendingApprovals = []
      finishOpenItems(snapshot, event.timestamp, 'cancelled')
      break
    default:
      break
  }
  return snapshot
}

/** 运行失败或取消时收敛仍未结束的子项。 */
function finishOpenItems(
  snapshot: RunSnapshot,
  timestamp: string,
  status: 'failed' | 'cancelled',
): void {
  const active = ['pending', 'running', 'waiting_approval']
  for (const agent of snapshot.agents) {
    if (active.includes(agent.status)) {
      agent.status = status
      agent.completedAt = timestamp
    }
  }
  for (const step of snapshot.plan?.steps ?? []) {
    if (['pending', 'running'].includes(step.status)) step.status = status
  }
  for (const tool of snapshot.toolCalls) {
    if (active.includes(tool.status)) {
      tool.status = status
      tool.completedAt = timestamp
    }
  }
  snapshot.pendingApprovals = []
}

