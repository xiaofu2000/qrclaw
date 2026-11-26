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
  /** 工具调用参数的一行摘要（命令、路径、搜索词等），没有可读参数时为空。 */
  toolSummary?: string
  /** 工具执行状态与上报的记忆页，来自同一调用 ID 的「工具完成」事件。 */
  toolState?: 'completed' | 'failed'
  toolPages?: string[]
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
  tool_calls?: Array<{ id: string; function?: { name: string; arguments?: string } }>
}

export type Conversation = {
  id: string
  title: string
  updatedAt: string
  messageCount?: number
  lastReplyId?: string | null
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
  last_reply_id?: string | null
}

export type SessionDetail = {
  id: string
  title: string
  history_version: number
  message_count: number
  updated_at: string
  run_status: SessionSummary['run_status']
  active_subagents?: boolean
  last_reply_id?: string | null
  run_error: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  context_tokens?: number
  context_limit?: number
  compression_threshold?: number
  /** 增量读取时 messages / events 只含偏移之后的部分；旧服务不返回时按完整内容处理。 */
  event_count?: number
  messages_offset?: number
  events_offset?: number
  messages: MessagePayload[]
  events?: SessionEvent[]
}

/** 会话过程事件；状态事件用 meta.code 判断，标题只用于显示。 */
export type SessionEvent = {
  kind: string
  title: string
  content?: string
  collapsed?: boolean
  created_at?: string
  meta?: {
    code?: 'plan_started' | 'plan_failed' | 'step_running' | 'step_completed' | 'step_failed' | 'replanning' | 'replanned' | 'run_failed' | 'run_cancelled'
    call_id?: string
    pages?: string[]
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
}
