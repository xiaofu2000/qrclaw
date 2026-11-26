/**
 * 聊天工作区的纯数据规则：不含 React 状态，ViewModel、属性栏和测试共用。
 * 会话详情可能是增量读取的结果，合并规则也放在这里，保证所有读取入口一致。
 */
import type { AgentActivity, ChatTimelineItem, Conversation, Message, SessionDetail, SessionEvent, SessionSummary, SubAgentCard } from '../../lib/types'
import type { SessionCursor } from '../../lib/api'

const READ_REPLIES_KEY = 'qrclaw.readReplies'
/** 每个会话最多记住的已读回复数；删改末轮后回到更早的回复时仍能判断已读。 */
const READ_REPLIES_PER_SESSION = 20

export type ReadReplies = Record<string, string[]>

/** 只保存回复标识；旧浏览器记录损坏时不影响继续聊天。 */
export function loadReadReplies(): ReadReplies {
  try {
    const value = JSON.parse(localStorage.getItem(READ_REPLIES_KEY) ?? '{}')
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, string[]] =>
      Array.isArray(entry[1]) && entry[1].every(id => typeof id === 'string')))
  } catch { return {} }
}

/** 写回已读记录：每个会话只保留最近若干条，只保留仍存在的会话；存储不可用时只影响刷新后的提示。 */
export function saveReadReplies(replies: ReadReplies, existingIds?: Set<string>): ReadReplies {
  const next = Object.fromEntries(Object.entries(replies)
    .filter(([id, ids]) => ids.length && (!existingIds || existingIds.has(id)))
    .map(([id, ids]) => [id, ids.slice(-READ_REPLIES_PER_SESSION)]))
  try { localStorage.setItem(READ_REPLIES_KEY, JSON.stringify(next)) }
  catch { console.warn('无法保存已读状态，本次页面仍可使用。') }
  return next
}

/** 与列表接口使用相同的末轮回复规则，兼容尚未升级的本地服务。 */
export function latestReplyId(session: SessionDetail | null): string | null {
  if (!session) return null
  const userIndex = session.messages.findLastIndex(message => message.role === 'user')
  const replyIndex = session.messages.findLastIndex(message => message.role === 'assistant'
    && message.kind !== 'execution_report' && !message.tool_calls?.length && typeof message.content === 'string' && message.content.trim())
  if (userIndex < 0 || replyIndex <= userIndex) return null
  return session.messages[replyIndex].uuid ?? `legacy:${session.history_version ?? 0}:${replyIndex}`
}

/** 从当前轮次推导真实工作状态，避免旧工具事件和其他会话驱动当前精灵。 */
export function getAgentActivity({ session, cards, running, streaming, error, loading, creating, mutating, backgroundCount, unread }: {
  session: SessionDetail | null
  cards: SubAgentCard[]
  running: boolean
  streaming: string
  error: string
  loading: boolean
  creating: boolean
  mutating: boolean
  backgroundCount: number
  unread: boolean
}): AgentActivity {
  if (error) return { phase: 'error', label: '需要检查', status: 'error' }
  if (loading || creating || mutating) return {
    phase: 'loading', label: loading ? '载入会话中' : creating ? '新建会话中' : '更新会话中', status: 'busy',
  }
  const userIndex = session?.messages.findLastIndex(message => message.role === 'user') ?? -1
  const events = (session?.events ?? []).slice(session?.messages[userIndex]?.turn_event_index ?? 0)
  if (running || session?.active_subagents) {
    const compression = events.findLast(event => event?.kind === 'compression')
    if (compression?.meta?.state === 'started') return { phase: 'compressing', label: '整理上下文', status: 'busy' }
    const tool = events.findLast(event => event?.kind === 'tool_status')
    if (tool?.meta?.state === 'started') return { phase: 'tool', label: '正在执行工具', status: 'busy' }
    if (session?.active_subagents || cards.some(card => card.state === 'running' && card.afterMessageIndex >= userIndex)) {
      return { phase: 'subagent', label: '子任务执行中', status: 'busy' }
    }
    if (streaming) return { phase: 'responding', label: '正在回复', status: 'busy' }
    if (session?.run_status === 'queued' && !events.some(Boolean)) return { phase: 'queued', label: '等待执行', status: 'busy' }
    const status = events.findLast(event => event?.kind === 'status')
    return { phase: 'thinking', label: status?.meta?.code === 'replanning' ? '正在调整计划' : '正在思考', status: 'busy' }
  }
  if (backgroundCount) return { phase: 'background', label: `${backgroundCount} 个后台任务处理中`, status: 'busy' }
  if (unread) return { phase: 'completed', label: '本轮已完成', status: 'ready' }
  return { phase: 'idle', label: '可以开始', status: 'ready' }
}

export function toConversation(session: SessionSummary): Conversation {
  return { id: session.id, title: session.title || '新对话', updatedAt: session.updated_at, messageCount: session.message_count,
    lastReplyId: session.run_status === 'idle' && !session.active_subagents ? session.last_reply_id : null }
}

/** 参数里最能说明这次调用做了什么的字段，按常见工具的参数名依次查找。 */
const SUMMARY_KEYS = ['command', 'cmd', 'path', 'file_path', 'filePath', 'query', 'url', 'name', 'pattern', 'projectPath', 'content']

/** 把工具参数压成一行摘要；参数不是 JSON 或没有可读字段时返回空。 */
export function summarizeToolArguments(raw?: string): string {
  if (!raw) return ''
  let args: unknown
  try { args = JSON.parse(raw) } catch { return '' }
  if (!args || typeof args !== 'object') return ''
  const record = args as Record<string, unknown>
  const key = SUMMARY_KEYS.find(name => typeof record[name] === 'string' && (record[name] as string).trim())
    ?? Object.keys(record).find(name => typeof record[name] === 'string' && (record[name] as string).trim())
  if (!key) return ''
  const text = (record[key] as string).replace(/\s+/g, ' ').trim()
  return text.length > 120 ? `${text.slice(0, 120)}…` : text
}

/** 整理消息：去掉只含工具调用的空回复，按调用 ID 附上工具名、参数摘要和完成事件里的结果。 */
export function normalizeMessages(sessionId: string, messages: SessionDetail['messages'], events: SessionEvent[] = []): Message[] {
  const calls = new Map<string, { name: string; summary: string }>()
  const finished = new Map<string, SessionEvent>()
  for (const event of events) {
    const callId = event?.kind === 'tool_status' ? event.meta?.call_id : undefined
    if (callId && (event.meta?.state === 'completed' || event.meta?.state === 'failed')) finished.set(callId, event)
  }
  return messages.flatMap((message, index) => {
    for (const call of message.tool_calls ?? []) calls.set(call.id, { name: call.function?.name ?? '工具', summary: summarizeToolArguments(call.function?.arguments) })
    if (message.role === 'assistant' && message.tool_calls?.length && !message.content) return []
    const call = message.tool_call_id ? calls.get(message.tool_call_id) : undefined
    const done = message.tool_call_id ? finished.get(message.tool_call_id) : undefined
    return [{
      id: `${sessionId}-${index}`,
      uuid: message.uuid,
      sourceIndex: index,
      role: message.role as Message['role'],
      kind: message.kind,
      content: typeof message.content === 'string' ? message.content : JSON.stringify(message.content ?? ''),
      toolName: call?.name,
      toolSummary: call?.summary || undefined,
      toolState: done ? (done.meta?.state === 'failed' ? 'failed' : 'completed') : undefined,
      toolPages: done?.meta?.pages?.length ? done.meta.pages : undefined,
    }]
  })
}

/** 旧会话没有轮次索引时，仅用完整的消息保存事件推断末轮过程起点。 */
export function turnEventStart(session: SessionDetail, userIndex: number): number {
  const marker = session.messages[userIndex].turn_event_index
  if (marker !== undefined) return marker
  const prior = session.messages.slice(0, userIndex)
    .filter(message => (message.role === 'assistant' || message.role === 'tool') && message.kind !== 'execution_report').length
  const saved = (session.events ?? []).flatMap((event, index) => event.kind === 'message_saved' ? [index] : [])
  const total = session.messages.filter(message => (message.role === 'assistant' || message.role === 'tool') && message.kind !== 'execution_report').length
  return saved.length === total && prior > 0 ? saved[prior - 1] + 1 : 0
}

/** 从父会话事件还原每次子任务的卡片及最终状态。 */
export function subAgentCards(session: SessionDetail, childSessions: Record<string, SessionDetail>): SubAgentCard[] {
  const cards: SubAgentCard[] = []
  const byId = new Map<string, SubAgentCard>()
  for (const event of session.events ?? []) {
    if (!event || event.kind !== 'subagent') continue
    const childSessionId = event.meta?.child_session_id
    if (!childSessionId) continue
    if (event.meta?.state === 'running') {
      if (byId.has(childSessionId)) continue
      const card: SubAgentCard = {
        id: `subagent-${childSessionId}`, childSessionId, agentId: event.meta.agent_id ?? '子 Agent',
        task: event.content ?? '', state: 'running', afterMessageIndex: event.meta.after_message_index ?? -1,
      }
      cards.push(card)
      byId.set(childSessionId, card)
    } else {
      const card = byId.get(childSessionId)
      if (card && (event.meta?.state === 'completed' || event.meta?.state === 'failed' || event.meta?.state === 'interrupted')) card.state = event.meta.state
    }
  }
  return cards.map(card => card.state === 'running' && childSessions[card.childSessionId]?.run_status === 'idle'
    ? { ...card, state: 'interrupted' } : card)
}

/** 按原始消息位置插入卡片，避免把子任务内容加入模型上下文。 */
export function buildTimeline(session: SessionDetail | null, messages: Message[], cards: SubAgentCard[]): ChatTimelineItem[] {
  if (!session) return messages
  const after = new Map<number, SubAgentCard[]>()
  for (const card of cards) after.set(card.afterMessageIndex, [...(after.get(card.afterMessageIndex) ?? []), card])
  const timeline: ChatTimelineItem[] = [...(after.get(-1) ?? [])]
  let next = 0
  for (let index = 0; index < session.messages.length; index++) {
    while (messages[next]?.sourceIndex === index) timeline.push(messages[next++])
    timeline.push(...(after.get(index) ?? []))
  }
  return timeline
}

/** 服务端上次确认的长度；本地可能有乐观消息或过程流写入的空洞，不能直接用本地数组长度。 */
export type ServerCounts = { historyVersion: number; messages: number; events: number }

export function sessionCursor(counts: ServerCounts | undefined): SessionCursor | undefined {
  return counts ? { historyVersion: counts.historyVersion, messages: counts.messages, events: counts.events } : undefined
}

/**
 * 把读取结果拼成完整会话。完整读取直接返回；增量读取需要本地有同一历史版本且长度够长的缓存，
 * 否则返回 null，由调用方改为完整读取，避免把新增部分接到错误的历史上。
 */
export function mergeSessionDelta(cached: SessionDetail | undefined, detail: SessionDetail): SessionDetail | null {
  const messagesOffset = detail.messages_offset ?? 0, eventsOffset = detail.events_offset ?? 0
  if (!messagesOffset && !eventsOffset) return detail
  if (!cached || (cached.history_version ?? 0) !== (detail.history_version ?? 0)
    || cached.messages.length < messagesOffset || (cached.events?.length ?? 0) < eventsOffset) return null
  return {
    ...detail,
    messages: [...cached.messages.slice(0, messagesOffset), ...detail.messages],
    events: [...(cached.events ?? []).slice(0, eventsOffset), ...(detail.events ?? [])],
    messages_offset: 0, events_offset: 0,
  }
}

/** 记录一次服务端回复后的完整长度，下次增量读取从这里开始。 */
export function serverCounts(detail: SessionDetail): ServerCounts {
  return {
    historyVersion: detail.history_version ?? 0,
    messages: (detail.messages_offset ?? 0) + detail.messages.length,
    events: detail.event_count ?? (detail.events_offset ?? 0) + (detail.events?.length ?? 0),
  }
}

/** 属性栏「最近记录」里展示的步骤状态。 */
const STEP_CODES = new Set(['step_running', 'step_completed', 'step_failed', 'run_cancelled'])

/** 主会话与子会话的过程记录：工具状态、上下文整理和步骤状态（子会话额外保留失败原因）。 */
export function progressEvents(session: SessionDetail | null | undefined, child = false): SessionEvent[] {
  return (session?.events ?? []).filter(event => event?.kind === 'tool_status' || event?.kind === 'compression'
    || (event?.kind === 'status' && (STEP_CODES.has(event.meta?.code ?? '') || (child && event.meta?.code === 'run_failed'))))
}

export type SessionStats = {
  toolTotal: number
  toolList: Array<[string, number]>
  memoryWrites: number
  memoryPages: string[]
  compressions: SessionEvent[]
}

/** 从会话事件统计工具调用、上下文整理和写入的记忆页，只读取已有接口字段。 */
export function sessionStats(session?: SessionDetail | null): SessionStats {
  const tools = new Map<string, number>()
  const pages = new Set<string>()
  for (const event of session?.events ?? []) {
    if (event?.kind !== 'tool_status') continue
    if (event.meta?.state === 'started') {
      const name = event.meta.tool ?? event.content ?? '工具'
      tools.set(name, (tools.get(name) ?? 0) + 1)
    }
    for (const page of event.meta?.pages ?? []) pages.add(page)
  }
  const toolList = [...tools].sort((a, b) => b[1] - a[1])
  return {
    toolTotal: toolList.reduce((sum, [, count]) => sum + count, 0), toolList,
    memoryWrites: tools.get('write_wiki_page') ?? 0, memoryPages: [...pages],
    compressions: (session?.events ?? []).filter(event => event?.kind === 'compression' && event.meta?.state === 'completed'),
  }
}
