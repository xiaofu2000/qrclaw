import type {
  AgentDto,
  ConversationDto,
  MessageDto,
  RunSnapshotDto,
  SettingsDto,
} from './protocol'
import type { Agent, Conversation, Message, RunSnapshot, Settings } from './workbench'

/** 把会话 DTO 转换为页面领域模型。 */
export function mapConversation(dto: ConversationDto): Conversation {
  return {
    id: dto.conversation_id,
    title: dto.title,
    workspacePath: dto.workspace_path,
    createdAt: dto.created_at,
    updatedAt: dto.updated_at,
  }
}

/** 把后端可能返回的多形态消息正文收敛为可展示文本。 */
function normalizeContent(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'text' in item) return String(item.text)
        return JSON.stringify(item)
      })
      .join('\n')
  }
  return content == null ? '' : JSON.stringify(content)
}

/** 把历史消息 DTO 转换为页面消息。 */
export function mapMessage(dto: MessageDto): Message {
  const allowedRoles = ['user', 'assistant', 'tool', 'system'] as const
  const role = allowedRoles.find((item) => item === dto.role) ?? 'assistant'
  return { id: dto.message_id, role, content: normalizeContent(dto.content) }
}

/** 把 Agent DTO 转换为页面领域模型。 */
export function mapAgent(dto: AgentDto): Agent {
  return {
    id: dto.agent_id,
    parentAgentId: dto.parent_agent_id,
    runId: dto.run_id,
    stepId: dto.step_id,
    name: dto.name,
    task: dto.task,
    status: dto.status,
    currentAction: dto.current_action,
    decisionSummary: dto.decision_summary,
    startedAt: dto.started_at,
    completedAt: dto.completed_at,
    result: dto.result,
    error: dto.error,
  }
}

/** 把运行快照 DTO 转换为页面领域模型。 */
export function mapRunSnapshot(dto: RunSnapshotDto): RunSnapshot {
  return {
    run: {
      id: dto.run.run_id,
      conversationId: dto.run.conversation_id,
      status: dto.run.status,
      route: dto.run.route,
      goal: dto.run.goal,
      createdAt: dto.run.created_at,
      updatedAt: dto.run.updated_at,
      lastSeq: dto.run.last_seq,
      error: dto.run.error,
    },
    plan: dto.plan
      ? {
          id: dto.plan.plan_id,
          goal: dto.plan.goal,
          projectPath: dto.plan.project_path,
          revision: dto.plan.revision,
          steps: dto.plan.steps.map((step) => ({
            id: step.step_id,
            description: step.description,
            dependsOn: step.depends_on,
            status: step.status,
            output: step.output,
          })),
        }
      : null,
    agents: dto.agents.map(mapAgent),
    toolCalls: dto.tool_calls.map((tool) => ({
      id: tool.tool_call_id,
      runId: tool.run_id,
      agentId: tool.agent_id,
      name: tool.name,
      arguments: tool.arguments,
      status: tool.status,
      startedAt: tool.started_at,
      completedAt: tool.completed_at,
      result: tool.result,
      error: tool.error,
    })),
    pendingApprovals: dto.pending_approvals.map((approval) => ({
      id: approval.approval_id,
      toolCallId: approval.tool_call_id,
      runId: approval.run_id,
      agentId: approval.agent_id,
      createdAt: approval.created_at,
      details: approval.details,
    })),
    messages: dto.messages.map((message) => ({
      id: message.message_id,
      content: message.content,
      completed: message.completed,
    })),
    fileChanges: dto.file_changes,
    usage: dto.usage,
  }
}

/** 把设置 DTO 转换为页面领域模型。 */
export function mapSettings(dto: SettingsDto): Settings {
  return {
    provider: dto.provider,
    model: dto.model,
    baseUrl: dto.base_url,
    hasApiKey: dto.has_api_key,
    apiKeyMasked: dto.api_key_masked,
    defaultWorkspace: dto.default_workspace,
    logLevel: dto.log_level,
    sandboxEnabled: dto.sandbox_enabled,
  }
}

