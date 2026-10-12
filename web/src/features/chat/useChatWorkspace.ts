import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { createApiClient } from '../../lib/api'
import type { ChatTimelineItem, Conversation, Message, SessionDetail, SessionSummary, SubAgentCard } from '../../lib/types'

type ApiClient = ReturnType<typeof createApiClient>

function toConversation(session: SessionSummary): Conversation {
  return { id: session.id, title: session.message_count ? session.title || '新对话' : '新对话', updatedAt: session.updated_at, messageCount: session.message_count }
}

function normalizeMessages(sessionId: string, messages: SessionDetail['messages']): Message[] {
  const toolNames = new Map<string, string>()
  return messages.flatMap((message, index) => {
    for (const call of message.tool_calls ?? []) toolNames.set(call.id, call.function?.name ?? '工具')
    if (message.role === 'assistant' && message.tool_calls?.length && !message.content) return []
    return [{
      id: `${sessionId}-${index}`,
      sourceIndex: index,
      role: message.role as Message['role'],
      kind: message.kind,
      content: typeof message.content === 'string' ? message.content : JSON.stringify(message.content ?? ''),
      toolName: message.tool_call_id ? toolNames.get(message.tool_call_id) : undefined,
    }]
  })
}

/** 从父会话事件还原每次子任务的卡片及最终状态。 */
function subAgentCards(session: SessionDetail, childSessions: Record<string, SessionDetail>): SubAgentCard[] {
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
      if (card && (event.meta?.state === 'completed' || event.meta?.state === 'failed')) card.state = event.meta.state
    }
  }
  return cards.map(card => card.state === 'running' && childSessions[card.childSessionId]?.run_status === 'idle'
    ? { ...card, state: 'interrupted' } : card)
}

/** 按原始消息位置插入卡片，避免把子任务内容加入模型上下文。 */
function buildTimeline(session: SessionDetail | null, messages: Message[], cards: SubAgentCard[]): ChatTimelineItem[] {
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

/** 按会话维护聊天、过程事件和断线恢复状态。 */
export function useChatWorkspace(api: ApiClient) {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState('')
  const [sessions, setSessions] = useState<Record<string, SessionDetail>>({})
  const [streaming, setStreaming] = useState<Record<string, string>>({})
  const [childStreaming, setChildStreaming] = useState<Record<string, string>>({})
  const [selectedChildren, setSelectedChildren] = useState<Record<string, string>>({})
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [connectionError, setConnectionError] = useState('')
  const [connecting, setConnecting] = useState(true)
  const [creating, setCreating] = useState(false)
  const [loadingIds, setLoadingIds] = useState<Set<string>>(new Set())
  const [runningIds, setRunningIds] = useState<Set<string>>(new Set())
  const loadingRef = useRef(new Set<string>())
  const runningRef = useRef(new Set<string>())
  const requestsRef = useRef(new Set<string>())
  const creatingRef = useRef(false)
  const eventCursorsRef = useRef(new Map<string, number>())
  const eventStreamsRef = useRef(new Map<string, AbortController>())
  const streamRetryRef = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const loadingChildrenRef = useRef(new Set<string>())
  const streamBaselinesRef = useRef(new Map<string, number>())

  const storeSession = useCallback((session: SessionDetail) => {
    eventCursorsRef.current.set(session.id, Math.max(eventCursorsRef.current.get(session.id) ?? -1, (session.events?.length ?? 0) - 1))
    setSessions(current => ({ ...current, [session.id]: {
      ...session,
      events: (current[session.id]?.events?.length ?? 0) > (session.events?.length ?? 0)
        ? current[session.id].events : session.events,
    } }))
  }, [])

  /** 按服务端序号合并事件，忽略重连时重发的旧事件。 */
  const mergeEvent = useCallback((id: string, index: number, event: NonNullable<SessionDetail['events']>[number]) => {
    if (index <= (eventCursorsRef.current.get(id) ?? -1)) return
    eventCursorsRef.current.set(id, index)
    setSessions(current => {
      if (!current[id]) return current
      const events = [...(current[id].events ?? [])]
      events[index] = event
      return { ...current, [id]: { ...current[id], events } }
    })
  }, [])

  /** 连接只读过程流；断线后按会话状态重连，不重发聊天请求。 */
  const startEventStream = useCallback((id: string, child = false) => {
    if (eventStreamsRef.current.has(id) || streamRetryRef.current.has(id)) return
    const controller = new AbortController()
    eventStreamsRef.current.set(id, controller)
    void api.streamEvents(id, eventCursorsRef.current.get(id) ?? -1, (index, event) => {
      mergeEvent(id, index, event)
      if (event.kind === 'message_saved') {
        void api.getSession(id).then(storeSession).catch(() => { /* 下一次读取会话时恢复。 */ })
      }
      if (!child && event.kind === 'subagent' && event.meta?.state === 'running' && event.meta.child_session_id) {
        void api.getSession(event.meta.child_session_id).then(storeSession).catch(() => { /* 卡片仍可通过刷新恢复。 */ })
      }
    }, controller.signal, child ? text => setChildStreaming(current => ({ ...current, [id]: text })) : undefined)
      .catch(() => { /* 过程流中断后读取已保存的会话，不重新执行任务。 */ })
      .finally(() => {
        const current = eventStreamsRef.current.get(id) === controller
        if (current) eventStreamsRef.current.delete(id)
        void api.getSession(id).then(storeSession).catch(() => { /* 后续刷新仍可恢复。 */ })
        if (child && current) setChildStreaming(previous => ({ ...previous, [id]: '' }))
        if (current && !controller.signal.aborted) {
          const timer = setTimeout(() => {
            streamRetryRef.current.delete(id)
            void api.getSession(id).then(detail => {
              storeSession(detail)
              if (detail.run_status !== 'idle' || (!child && detail.active_subagents)) startEventStream(id, child)
            }).catch(() => { /* 下次打开会话时恢复。 */ })
          }, 1200)
          streamRetryRef.current.set(id, timer)
        }
      })
  }, [api, mergeEvent, storeSession])

  useEffect(() => () => {
    for (const controller of eventStreamsRef.current.values()) controller.abort()
    for (const timer of streamRetryRef.current.values()) clearTimeout(timer)
  }, [])

  const refreshConversations = useCallback(async () => {
    const listed = await api.listSessions()
    setConversations(listed.map(toConversation))
    runningRef.current = new Set([...requestsRef.current, ...listed.filter(item => item.run_status === 'queued' || item.run_status === 'running').map(item => item.id)])
    setRunningIds(new Set(runningRef.current))
    setConnectionError('')
  }, [api])

  useEffect(() => {
    let cancelled = false
    async function connect() {
      try {
        await api.health()
        const listed = await api.listSessions()
        if (cancelled) return
        setConversations(listed.map(toConversation))
        runningRef.current = new Set(listed.filter(item => item.run_status === 'queued' || item.run_status === 'running').map(item => item.id))
        setRunningIds(new Set(runningRef.current))
        const session = listed.length ? await api.getSession(listed[0].id) : await api.createSession()
        if (cancelled) return
        storeSession(session)
        setActiveId(session.id)
        if (!listed.length) await refreshConversations()
      } catch (error) {
        if (!cancelled) setConnectionError(`连接失败：${error instanceof Error ? error.message : String(error)}`)
      } finally {
        if (!cancelled) setConnecting(false)
      }
    }
    void connect()
    return () => { cancelled = true }
  }, [api, storeSession, refreshConversations])

  async function loadConversation(id: string) {
    setActiveId(id)
    if ((runningRef.current.has(id) && sessions[id]) || loadingRef.current.has(id)) return
    loadingRef.current.add(id)
    setLoadingIds(new Set(loadingRef.current))
    setErrors(current => ({ ...current, [id]: '' }))
    try {
      const session = await api.getSession(id)
      storeSession(session)
      if (session.run_status !== 'idle') {
        runningRef.current.add(id)
        setRunningIds(new Set(runningRef.current))
      }
    } catch (error) {
      setErrors(current => ({ ...current, [id]: `会话载入失败：${error instanceof Error ? error.message : String(error)}` }))
    } finally {
      loadingRef.current.delete(id)
      setLoadingIds(new Set(loadingRef.current))
    }
  }

  async function newConversation() {
    if (creatingRef.current || connecting) return
    creatingRef.current = true
    setCreating(true)
    try {
      const session = await api.createSession()
      storeSession(session)
      setActiveId(session.id)
      try { await refreshConversations() } catch { setConnectionError('会话已创建，但列表刷新失败，请稍后重试。') }
    } catch (error) {
      setConnectionError(`新建会话失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      creatingRef.current = false
      setCreating(false)
    }
  }

  async function send(content: string) {
    const id = activeId
    content = content.trim()
    if (!id || !content || runningRef.current.has(id) || loadingRef.current.has(id) || creatingRef.current || connecting || !sessions[id]) return
    runningRef.current.add(id)
    requestsRef.current.add(id)
    setRunningIds(new Set(runningRef.current))
    setConversations(current => current.map(item => item.id === id && !item.messageCount
      ? { ...item, title: content.replace(/\s+/g, ' ').slice(0, 60), messageCount: 1 } : item))
    setErrors(current => ({ ...current, [id]: '' }))
    setDrafts(current => ({ ...current, [id]: '' }))
    setStreaming(current => ({ ...current, [id]: '' }))
    streamBaselinesRef.current.set(id, sessions[id].messages.length)
    setSessions(current => ({ ...current, [id]: {
      ...current[id], run_status: 'queued', run_error: null, messages: [...current[id].messages, { role: 'user', content }],
    } }))
    let completed = false
    let accepted = false
    try {
      await api.chatStream(id, content, delta => {
        setStreaming(current => ({ ...current, [id]: (current[id] ?? '') + delta }))
      }, () => {
        accepted = true
        startEventStream(id)
      })
      completed = true
      storeSession(await api.getSession(id))
      setStreaming(current => ({ ...current, [id]: '' }))
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      setErrors(current => ({ ...current, [id]: completed ? `任务已完成，但读取结果失败：${detail}。请刷新会话。` : `请求失败：${detail}` }))
      if (!completed) {
        if (!accepted) setDrafts(current => ({ ...current, [id]: current[id] || content }))
        try {
          const latest = await api.getSession(id)
          if (latest.run_status === 'idle') {
            storeSession(latest)
            setStreaming(current => ({ ...current, [id]: '' }))
            if (accepted) setErrors(current => ({ ...current, [id]: '' }))
          } else if (!accepted) storeSession(latest)
          else setSessions(current => ({ ...current, [id]: { ...current[id], run_status: latest.run_status, events: latest.events } }))
        } catch { /* 状态不明时保留运行标记，避免重复提交。 */ }
      }
    } finally {
      requestsRef.current.delete(id)
      try { await refreshConversations() } catch { setConnectionError('会话列表刷新失败，请稍后重试。') }
    }
  }

  const activeSession = sessions[activeId] ?? null
  const cards = useMemo(() => activeSession ? subAgentCards(activeSession, sessions) : [], [activeSession, sessions])
  const selectedChildId = selectedChildren[activeId] ?? ''

  useEffect(() => {
    if (!activeSession) return
    if (!requestsRef.current.has(activeId) &&
      (activeSession.run_status !== 'idle' || activeSession.active_subagents)) startEventStream(activeId)
    for (const card of cards) {
      if (card.state !== 'running' || sessions[card.childSessionId] || loadingChildrenRef.current.has(card.childSessionId)) continue
      loadingChildrenRef.current.add(card.childSessionId)
      void api.getSession(card.childSessionId).then(storeSession).catch(() => { /* 选中卡片时可以重试读取。 */ })
        .finally(() => loadingChildrenRef.current.delete(card.childSessionId))
    }
  }, [activeId, activeSession, cards, sessions, api, storeSession, startEventStream])

  useEffect(() => {
    if (!selectedChildId) return
    let cancelled = false
    const streams = eventStreamsRef.current
    void api.getSession(selectedChildId).then(detail => {
      if (cancelled) return
      storeSession(detail)
      if (detail.run_status === 'running') startEventStream(selectedChildId, true)
    }).catch(() => { /* 子任务详情保留当前内容，用户可重新点开。 */ })
    return () => {
      cancelled = true
      setChildStreaming(current => ({ ...current, [selectedChildId]: '' }))
      const controller = streams.get(selectedChildId)
      if (controller) {
        streams.delete(selectedChildId)
        controller.abort()
      }
    }
  }, [activeId, selectedChildId, api, storeSession, startEventStream])

  useEffect(() => {
    if (connecting || !runningIds.size) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      let completed = false
      await Promise.all([...runningIds].map(async id => {
        try {
          const session = await api.getSession(id)
          if (cancelled) return
          if (session.run_status === 'idle' && !requestsRef.current.has(id)) {
            storeSession(session)
            setStreaming(current => ({ ...current, [id]: '' }))
            setErrors(current => ({ ...current, [id]: '' }))
            runningRef.current.delete(id)
            completed = true
          } else {
            const cursor = eventCursorsRef.current.get(id) ?? -1
            const savedEvents = session.events ?? []
            if (savedEvents.length - 1 > cursor) eventCursorsRef.current.set(id, savedEvents.length - 1)
            setSessions(current => current[id] ? ({ ...current, [id]: {
              ...current[id], events: savedEvents.length - 1 >= cursor ? savedEvents : current[id].events,
              total_tokens: session.total_tokens, run_status: session.run_status, active_subagents: session.active_subagents,
            } }) : ({ ...current, [id]: session }))
          }
        } catch { /* 保留运行状态，等后续轮询确认结果。 */ }
      }))
      if (!cancelled && completed) {
        setRunningIds(new Set(runningRef.current))
        try { await refreshConversations() } catch { setConnectionError('会话列表刷新失败，请稍后重试。') }
      }
      if (!cancelled) timer = setTimeout(poll, 1200)
    }
    void poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [api, connecting, runningIds, storeSession, refreshConversations])

  const session = activeSession
  const rawMessages = session?.messages
  const messages = useMemo(() => normalizeMessages(activeId, rawMessages ?? []), [activeId, rawMessages])
  const timeline = useMemo(() => buildTimeline(session, messages, cards), [session, messages, cards])
  const selectedCard = cards.find(card => card.childSessionId === selectedChildId) ?? null
  const childSession = selectedChildId ? sessions[selectedChildId] ?? null : null
  const childMessages = useMemo(() => normalizeMessages(selectedChildId, childSession?.messages ?? []), [selectedChildId, childSession?.messages])
  const loading = connecting || loadingIds.has(activeId)
  const running = runningIds.has(activeId)
  const queued = running && session?.run_status === 'queued'
  const error = errors[activeId] || (session?.run_error ? `任务失败：${session.run_error}` : '') || connectionError

  return {
    conversations, activeId, session, messages, timeline, cards, selectedCard, childSession, childMessages,
    childStreaming: childStreaming[selectedChildId] ?? '', selectSubAgent: (id: string | null) =>
      setSelectedChildren(current => ({ ...current, [activeId]: id ?? '' })),
    streaming: (streaming[activeId] ?? '').slice((session?.messages ?? []).slice(streamBaselinesRef.current.get(activeId) ?? session?.messages.length ?? 0)
      .filter(message => message.role === 'assistant' && message.kind !== 'execution_report')
      .reduce((length, message) => length + (typeof message.content === 'string' ? message.content.length : 0), 0)),
    runningIds, running, queued, loading, creating, error,
    draft: drafts[activeId] ?? '',
    setDraft: (value: string) => setDrafts(current => ({ ...current, [activeId]: value })),
    loadConversation, newConversation, send,
    refresh: async () => {
      if (activeId) {
        await loadConversation(activeId)
        try { await refreshConversations() } catch { setConnectionError('会话列表刷新失败，请稍后重试。') }
      } else await newConversation()
    },
  }
}
