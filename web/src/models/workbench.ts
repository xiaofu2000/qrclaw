import type {
  AgentStatusDto,
  RunStatusDto,
  StepStatusDto,
  ToolCallStatusDto,
} from './protocol'

export type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'error'

export type Conversation = {
  id: string
  title: string
  workspacePath: string
  createdAt: string
  updatedAt: string
}

export type DirectoryListing = {
  currentPath: string
  parentPath: string | null
  homePath: string
  directories: Array<{ name: string; path: string }>
}

export type Message = {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
}

export type Run = {
  id: string
  conversationId: string
  status: RunStatusDto
  route: string | null
  goal: string
  createdAt: string
  updatedAt: string
  lastSeq: number
  error: Record<string, unknown> | null
}

export type PlanStep = {
  id: string
  description: string
  dependsOn: string[]
  status: StepStatusDto
  output: string | null
}

export type Plan = {
  id: string
  goal: string
  projectPath: string
  steps: PlanStep[]
  revision: number
}

export type Agent = {
  id: string
  parentAgentId: string | null
  runId: string
  stepId: string | null
  name: string
  task: string
  status: AgentStatusDto
  currentAction: string
  decisionSummary: string
  startedAt: string | null
  completedAt: string | null
  result: string | null
  error: string | null
}

export type ToolCall = {
  id: string
  runId: string
  agentId: string
  name: string
  arguments: Record<string, unknown>
  status: ToolCallStatusDto
  startedAt: string | null
  completedAt: string | null
  result: string | null
  error: string | null
}

export type Approval = {
  id: string
  toolCallId: string
  runId: string
  agentId: string
  createdAt: string
  details: Record<string, unknown>
}

export type AssistantMessage = {
  id: string
  content: string
  completed: boolean
}

export type RunSnapshot = {
  run: Run
  plan: Plan | null
  agents: Agent[]
  toolCalls: ToolCall[]
  pendingApprovals: Approval[]
  messages: AssistantMessage[]
  fileChanges: Record<string, unknown>[]
  usage: Record<string, unknown>
}

export type Settings = {
  provider: string
  model: string
  baseUrl: string
  hasApiKey: boolean
  apiKeyMasked: string
  defaultWorkspace: string
  logLevel: string
  sandboxEnabled: boolean
}

export type SettingsInput = {
  provider: string
  model: string
  baseUrl: string
  apiKey?: string
  defaultWorkspace: string
  logLevel: string
}

/** 判断运行是否仍接受取消命令或后续事件。 */
export function isRunActive(status: RunStatusDto | undefined): boolean {
  return Boolean(
    status &&
      ['queued', 'routing', 'running', 'waiting_approval', 'cancelling'].includes(status),
  )
}
