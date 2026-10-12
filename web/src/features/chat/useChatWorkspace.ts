import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { createApiClient } from '../../lib/api'
import type { Conversation, Message, SessionDetail, SessionSummary } from '../../lib/types'

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
      role: message.role as Message['role'],
      kind: message.kind,
      content: typeof message.content === 'string' ? message.content : JSON.stringify(message.content ?? ''),
      toolName: message.tool_call_id ? toolNames.get(message.tool_call_id) : undefined,
    }]
  })
}

/** 按会话维护聊天、过程事件和断线恢复状态。 */
export function useChatWorkspace(api: ApiClient) {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState('')
  const [sessions, setSessions] = useState<Record<string, SessionDetail>>({})
  const [streaming, setStreaming] = useState<Record<string, string>>({})
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

  const storeSession = useCallback((session: SessionDetail) => {
    eventCursorsRef.current.set(session.id, Math.max(eventCursorsRef.current.get(session.id) ?? -1, (session.events?.length ?? 0) - 1))
    setSessions(current => ({ ...current, [session.id]: session }))
  }, [])

  useEffect(() => () => { for (const controller of eventStreamsRef.current.values()) controller.abort() }, [])

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
        const controller = new AbortController()
        eventStreamsRef.current.set(id, controller)
        void api.streamEvents(id, eventCursorsRef.current.get(id) ?? -1, (index, event) => {
          if (index <= (eventCursorsRef.current.get(id) ?? -1)) return
          eventCursorsRef.current.set(id, index)
          setSessions(current => current[id] ? ({ ...current, [id]: {
            ...current[id], events: [...(current[id].events ?? []), event],
          } }) : current)
        }, controller.signal).catch(() => { /* 过程流失败时仍可通过会话查询恢复。 */ })
          .finally(() => eventStreamsRef.current.delete(id))
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
              total_tokens: session.total_tokens, run_status: session.run_status,
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

  const session = sessions[activeId] ?? null
  const rawMessages = session?.messages
  const messages = useMemo(() => normalizeMessages(activeId, rawMessages ?? []), [activeId, rawMessages])
  const loading = connecting || loadingIds.has(activeId)
  const running = runningIds.has(activeId)
  const queued = running && session?.run_status === 'queued'
  const error = errors[activeId] || (session?.run_error ? `任务失败：${session.run_error}` : '') || connectionError

  return {
    conversations, activeId, session, messages, streaming: streaming[activeId] ?? '', runningIds, running, queued, loading, creating, error,
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
