export type MessageRole = 'user' | 'assistant' | 'tool'

export type Message = {
  id: string
  uuid?: string
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
  uuid?: string
  turn_event_index?: number
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

/** 当前会话的工作阶段，由 ViewModel 推导，视图只负责选择动画。 */
export type AgentActivity = {
  phase: 'idle' | 'loading' | 'queued' | 'thinking' | 'tool' | 'subagent' | 'responding' | 'compressing' | 'completed' | 'error' | 'background'
  label: string
  status: RunStatus
}

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
  title: string
  history_version: number
  message_count: number
  updated_at: string
  run_status: SessionSummary['run_status']
  active_subagents?: boolean
  run_error: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  context_tokens?: number
  context_limit?: number
  compression_threshold?: number
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
      tokens?: number
      limit?: number
      compression_threshold?: number
      before_tokens?: number
      after_tokens?: number
      after_message_count?: number
      history_version?: number
    }
  }>
}
