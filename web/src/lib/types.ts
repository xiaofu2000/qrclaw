export type MessageRole = 'user' | 'assistant' | 'tool'

export type Message = {
  id: string
  role: MessageRole
  content: string
  sourceIndex?: number
  kind?: 'user' | 'assistant' | 'tool' | 'execution_report' | 'message'
  collapsed?: boolean
  toolName?: string
}

export type SubAgentCard = {
  id: string
  childSessionId: string
  agentId: string
  task: string
  state: 'running' | 'completed' | 'failed' | 'interrupted'
  afterMessageIndex: number
}

export type ChatTimelineItem = Message | SubAgentCard

export type MessagePayload = {
  role: MessageRole | string
  content: string
  kind?: Message['kind']
  tool_call_id?: string
  tool_calls?: Array<{ id: string; function?: { name: string } }>
}

export type Conversation = {
  id: string
  title: string
  updatedAt: string
  messageCount?: number
}

export type RunStatus = 'ready' | 'busy' | 'error'

export type SessionSummary = {
  id: string
  title?: string
  message_count: number
  updated_at: string
  run_status: 'idle' | 'queued' | 'running'
  active_subagents?: boolean
}

export type SessionDetail = {
  id: string
  message_count: number
  updated_at: string
  run_status: SessionSummary['run_status']
  active_subagents?: boolean
  run_error: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  messages: MessagePayload[]
  events?: Array<{
    kind: string
    title: string
    content?: string
    collapsed?: boolean
    created_at?: string
    meta?: {
      child_session_id?: string
      agent_id?: string
      state?: string
      after_message_index?: number
      tool?: string
    }
  }>
}
