export type MessageRole = 'user' | 'assistant' | 'tool'

export type Message = {
  id: string
  role: MessageRole
  content: string
  kind?: 'user' | 'assistant' | 'tool' | 'execution_report' | 'message'
  collapsed?: boolean
}

export type MessagePayload = {
  role: MessageRole | string
  content: string
  kind?: Message['kind']
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
  message_count: number
  updated_at: string
}

export type SessionDetail = {
  id: string
  message_count: number
  updated_at: string
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
  }>
}
