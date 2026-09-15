/** 后端统一成功响应。 */
export type ApiResponse<T> = {
  data: T
  request_id: string
}

/** 后端统一错误响应。 */
export type ApiErrorResponse = {
  error: {
    code: string
    message: string
    retryable: boolean
    details: Record<string, unknown>
  }
  request_id: string
}

export type RunStatusDto =
  | 'queued'
  | 'routing'
  | 'running'
  | 'waiting_approval'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type AgentStatusDto =
  | 'pending'
  | 'running'
  | 'waiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type StepStatusDto = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export type ToolCallStatusDto =
  | 'pending'
  | 'waiting_approval'
  | 'running'
  | 'completed'
  | 'failed'
  | 'denied'
  | 'cancelled'

export type ConversationDto = {
  conversation_id: string
  title: string
  workspace_path: string
  created_at: string
  updated_at: string
}

export type MessageDto = {
  message_id: string
  role: string
  content: unknown
  tool_calls?: unknown[] | null
}

export type RunDto = {
  run_id: string
  conversation_id: string
  status: RunStatusDto
  route: string | null
  goal: string
  created_at: string
  updated_at: string
  last_seq: number
  error: Record<string, unknown> | null
}

export type PlanStepDto = {
  step_id: string
  description: string
  depends_on: string[]
  status: StepStatusDto
  output: string | null
}

export type PlanDto = {
  plan_id: string
  goal: string
  project_path: string
  steps: PlanStepDto[]
  revision: number
}

export type AgentDto = {
  agent_id: string
  parent_agent_id: string | null
  run_id: string
  step_id: string | null
  name: string
  task: string
  status: AgentStatusDto
  current_action: string
  decision_summary: string
  started_at: string | null
  completed_at: string | null
  result: string | null
  error: string | null
}

export type ToolCallDto = {
  tool_call_id: string
  run_id: string
  agent_id: string
  name: string
  arguments: Record<string, unknown>
  status: ToolCallStatusDto
  started_at: string | null
  completed_at: string | null
  result: string | null
  error: string | null
}

export type ApprovalDto = {
  approval_id: string
  tool_call_id: string
  run_id: string
  agent_id: string
  status: string
  decision: string | null
  created_at: string
  resolved_at: string | null
  details: Record<string, unknown>
}

export type AssistantMessageDto = {
  message_id: string
  content: string
  completed: boolean
}

export type RunSnapshotDto = {
  run: RunDto
  plan: PlanDto | null
  agents: AgentDto[]
  tool_calls: ToolCallDto[]
  pending_approvals: ApprovalDto[]
  messages: AssistantMessageDto[]
  file_changes: Record<string, unknown>[]
  usage: Record<string, unknown>
}

export type EventEnvelopeDto = {
  version: string
  event_id: string
  seq: number
  timestamp: string
  conversation_id: string
  run_id: string
  agent_id: string | null
  parent_agent_id: string | null
  type: string
  data: Record<string, unknown>
}

export type CapabilitiesDto = {
  protocol_version: string
  features: string[]
  limits: Record<string, unknown>
}

export type DirectoryListingDto = {
  current_path: string
  parent_path: string | null
  home_path: string
  directories: Array<{ name: string; path: string }>
}

export type SettingsDto = {
  provider: string
  model: string
  base_url: string
  has_api_key: boolean
  api_key_masked: string
  default_workspace: string
  log_level: string
  sandbox_enabled: boolean
}
