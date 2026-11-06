import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { createApiClient } from '../../lib/api'
import type { Conversation, SessionDetail, SessionEvent, SessionSummary } from '../../lib/types'
import {
  buildTimeline, getAgentActivity, latestReplyId, loadReadReplies, mergeSessionDelta, normalizeMessages, saveReadReplies,
  serverCounts, sessionCursor, sessionStats, subAgentCards, toConversation, turnEventStart, type ServerCounts,
} from './chatModel'
import { useIdSet } from './useIdSet'

type ApiClient = ReturnType<typeof createApiClient>

/** 运行中会话的状态轮询间隔；详情只在完成或当前会话没有过程流时读取。 */
const POLL_INTERVAL = 1200
/** 首轮完成后等后台生成标题，再刷新一次会话列表。 */
const TITLE_REFRESH_DELAY = 5000

/** 按会话维护聊天、过程事件和断线恢复状态。 */
export function useChatWorkspace(api: ApiClient) {
  const [planEnabled, setPlanEnabled] = useState(() => {
    try { return localStorage.getItem('qrclaw.planEnabled') === 'true' }
    catch { return false }
  })
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState('')
  const [sessions, setSessions] = useState<Record<string, SessionDetail>>({})
  const [readReplies, setReadReplies] = useState(loadReadReplies)
  const [streaming, setStreaming] = useState<Record<string, string>>({})
  const [childStreaming, setChildStreaming] = useState<Record<string, string>>({})
  const [selectedChildren, setSelectedChildren] = useState<Record<string, string>>({})
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [connectionError, setConnectionError] = useState('')
  const [connecting, setConnecting] = useState(true)
  const [creating, setCreating] = useState(false)
  const loadingSet = useIdSet()
  const runningSet = useIdSet()
  const mutatingSet = useIdSet()
  // 只把稳定的函数放进依赖，集合对象本身每次渲染都会重建。
  const replaceRunning = runningSet.replace
  // 选择会话时立即更新，避免删除响应赶在重渲染前覆盖新的选择。
  const activeIdRef = useRef('')
  const requestsRef = useRef(new Set<string>())
  const creatingRef = useRef(false)
  const eventCursorsRef = useRef(new Map<string, number>())
  const eventStreamsRef = useRef(new Map<string, AbortController>())
  const streamRetryRef = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const loadingChildrenRef = useRef(new Set<string>())
  const streamBaselinesRef = useRef(new Map<string, number>())
  const historyVersionsRef = useRef(new Map<string, number>())
  const deletedIdsRef = useRef(new Set<string>())
  // 增量读取的依据：最近一次缓存的会话与服务端确认的长度。
  const sessionsRef = useRef<Record<string, SessionDetail>>({})
  const serverCountsRef = useRef(new Map<string, ServerCounts>())
  sessionsRef.current = sessions

  /** 视图只能确认当前已结束的回复，迟到的可见事件不能读掉下一轮或其他会话。 */
  const markReplyRead = useCallback((id: string, replyId: string) => {
    const detail = sessions[id]
    if (id !== activeIdRef.current || !detail || detail.run_status !== 'idle' || detail.active_subagents
      || runningSet.ref.current.has(id) || mutatingSet.ref.current.has(id) || latestReplyId(detail) !== replyId) return
    setReadReplies(current => {
      if (current[id]?.includes(replyId)) return current
      const saved = loadReadReplies()
      const next = { ...current }
      for (const [key, ids] of Object.entries(saved)) next[key] = [...new Set([...ids, ...(current[key] ?? [])])]
      next[id] = [...new Set([...(next[id] ?? []), replyId])]
      return saveReadReplies(next)
    })
  }, [sessions, runningSet.ref, mutatingSet.ref])

  /** 读取会话：有同一历史版本的缓存时只取新增部分，拼接不上就改为完整读取。 */
  const readSession = useCallback(async (id: string): Promise<SessionDetail> => {
    const counts = serverCountsRef.current.get(id), cached = sessionsRef.current[id]
    const detail = await api.getSession(id, cached ? sessionCursor(counts) : undefined)
    return mergeSessionDelta(cached, detail) ?? await api.getSession(id)
  }, [api])

  const storeSession = useCallback((session: SessionDetail) => {
    if (deletedIdsRef.current.has(session.id)) return
    const version = session.history_version ?? 0
    const previous = historyVersionsRef.current.get(session.id) ?? 0
    if (version < previous) return
    if (version > previous && requestsRef.current.has(session.id)) {
      const compression = session.events?.findLast(event => event?.kind === 'compression'
        && event.meta?.state === 'completed' && event.meta.history_version === version)
      if (compression?.meta?.after_message_count !== undefined) {
        streamBaselinesRef.current.set(session.id, compression.meta.after_message_count)
      }
    }
    historyVersionsRef.current.set(session.id, version)
    eventCursorsRef.current.set(session.id, version > previous ? (session.events?.length ?? 0) - 1
      : Math.max(eventCursorsRef.current.get(session.id) ?? -1, (session.events?.length ?? 0) - 1))
    setSessions(current => {
      const stored = {
        ...session,
        messages: version === previous && (current[session.id]?.messages.length ?? 0) > session.messages.length
          ? current[session.id].messages : session.messages,
        events: version === previous && (current[session.id]?.events?.length ?? 0) > (session.events?.length ?? 0)
          ? current[session.id].events : session.events,
      }
      // 缓存与服务端长度同时更新，增量读取不会拿新游标去拼旧缓存。
      sessionsRef.current = { ...sessionsRef.current, [session.id]: stored }
      serverCountsRef.current.set(session.id, serverCounts(session))
      return { ...current, [session.id]: stored }
    })
  }, [])

  /** 按服务端序号合并事件，忽略重连时重发的旧事件。 */
  const mergeEvent = useCallback((id: string, index: number, event: SessionEvent) => {
    if (index <= (eventCursorsRef.current.get(id) ?? -1)) return
    eventCursorsRef.current.set(id, index)
    setSessions(current => {
      if (!current[id]) return current
      const events = [...(current[id].events ?? [])]
      events[index] = event
      const detail = { ...current[id], events }
      if (event.kind === 'context_usage') {
        detail.context_tokens = event.meta?.tokens
        detail.context_limit = event.meta?.limit
        detail.compression_threshold = event.meta?.compression_threshold
      }
      if (event.kind === 'compression') {
        detail.context_tokens = event.meta?.after_tokens ?? event.meta?.before_tokens ?? detail.context_tokens
        if (event.meta?.state === 'started') detail.run_status = 'running'
      }
      return { ...current, [id]: detail }
    })
  }, [])

  /** 连接只读过程流；断线后按会话状态重连，不重发聊天请求。 */
  const startEventStream = useCallback((id: string, child = false) => {
    if (eventStreamsRef.current.has(id) || streamRetryRef.current.has(id)) return
    const controller = new AbortController()
    eventStreamsRef.current.set(id, controller)
    void api.streamEvents(id, eventCursorsRef.current.get(id) ?? -1, (index, event) => {
      if (controller.signal.aborted) return
      mergeEvent(id, index, event)
      if (event.kind === 'message_saved' || (event.kind === 'compression' && event.meta?.state === 'completed')) {
        void readSession(id).then(storeSession).catch(() => { /* 下一次读取会话时恢复。 */ })
      }
      if (!child && event.kind === 'subagent' && event.meta?.state === 'running' && event.meta.child_session_id) {
        void readSession(event.meta.child_session_id).then(storeSession).catch(() => { /* 卡片仍可通过刷新恢复。 */ })
      }
    }, controller.signal, child ? text => setChildStreaming(current => ({ ...current, [id]: text })) : undefined)
      .catch(() => { /* 过程流中断后读取已保存的会话，不重新执行任务。 */ })
      .finally(() => {
        const current = eventStreamsRef.current.get(id) === controller
        if (current) eventStreamsRef.current.delete(id)
        void readSession(id).then(storeSession).catch(() => { /* 后续刷新仍可恢复。 */ })
        if (child && current) setChildStreaming(previous => ({ ...previous, [id]: '' }))
        if (current && !controller.signal.aborted) {
          const timer = setTimeout(() => {
            streamRetryRef.current.delete(id)
            void readSession(id).then(detail => {
              storeSession(detail)
              if (detail.run_status !== 'idle' || (!child && detail.active_subagents)) startEventStream(id, child)
            }).catch(() => { /* 下次打开会话时恢复。 */ })
          }, 1200)
          streamRetryRef.current.set(id, timer)
        }
      })
  }, [api, mergeEvent, readSession, storeSession])

  useEffect(() => () => {
    for (const controller of eventStreamsRef.current.values()) controller.abort()
    for (const timer of streamRetryRef.current.values()) clearTimeout(timer)
  }, [])

  /** 用列表接口刷新会话、运行状态，并清理已删除会话的已读记录。 */
  const applyListing = useCallback((listed: SessionSummary[]) => {
    const available = listed.filter(item => !deletedIdsRef.current.has(item.id))
    const mapped = available.map(toConversation)
    setConversations(mapped)
    replaceRunning([...requestsRef.current, ...available.filter(item => item.run_status === 'queued' || item.run_status === 'running').map(item => item.id)])
    const existing = new Set(available.map(item => item.id))
    setReadReplies(current => Object.keys(current).some(id => !existing.has(id)) ? saveReadReplies(current, existing) : current)
    setConnectionError('')
    return mapped
  }, [replaceRunning])

  const refreshConversations = useCallback(async () => applyListing(await api.listSessions()), [api, applyListing])

  useEffect(() => {
    let cancelled = false
    async function connect() {
      try {
        await api.health()
        const listed = await api.listSessions()
        if (cancelled) return
        setConversations(listed.map(toConversation))
        replaceRunning(listed.filter(item => item.run_status === 'queued' || item.run_status === 'running').map(item => item.id))
        const session = listed.length ? await api.getSession(listed[0].id) : await api.createSession()
        if (cancelled) return
        storeSession(session)
        activeIdRef.current = session.id
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
  }, [api, storeSession, refreshConversations, replaceRunning])

  async function loadConversation(id: string) {
    if (deletedIdsRef.current.has(id)) return
    activeIdRef.current = id
    setActiveId(id)
    if (((runningSet.has(id) || mutatingSet.has(id)) && sessions[id]) || loadingSet.has(id)) return
    loadingSet.add(id)
    setErrors(current => ({ ...current, [id]: '' }))
    try {
      const session = await readSession(id)
      storeSession(session)
      if (session.run_status !== 'idle') runningSet.add(id)
    } catch (error) {
      setErrors(current => ({ ...current, [id]: `会话载入失败：${error instanceof Error ? error.message : String(error)}` }))
    } finally {
      loadingSet.remove(id)
    }
  }

  /** 已有空白会话时直接切过去，避免每点一次「新对话」就在服务端留下一个空会话。 */
  async function newConversation() {
    if (creatingRef.current || connecting) return
    const blank = conversations.find(item => !item.messageCount && !deletedIdsRef.current.has(item.id)
      && !runningSet.has(item.id) && !(sessions[item.id]?.messages.length))
    if (blank) {
      await loadConversation(blank.id)
      return
    }
    creatingRef.current = true
    setCreating(true)
    try {
      const session = await api.createSession()
      storeSession(session)
      activeIdRef.current = session.id
      setActiveId(session.id)
      try { await refreshConversations() } catch { setConnectionError('会话已创建，但列表刷新失败，请稍后重试。') }
    } catch (error) {
      setConnectionError(`新建会话失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      creatingRef.current = false
      setCreating(false)
    }
  }

  async function send(content: string, editMessageId?: string) {
    const id = activeId
    content = content.trim()
    if (!id || !content || runningSet.has(id) || mutatingSet.has(id) || loadingSet.has(id) || creatingRef.current || connecting || !sessions[id]) return
    const previous = sessions[id]
    const firstTurn = !previous.messages.some(message => message.role === 'user')
    const editIndex = editMessageId ? previous.messages.findLastIndex(message => message.role === 'user') : -1
    if (editMessageId && (editIndex < 0 || previous.messages[editIndex].uuid !== editMessageId)) return
    const editEventStart = editMessageId ? turnEventStart(previous, editIndex) : 0
    if (editMessageId) {
      eventStreamsRef.current.get(id)?.abort()
      eventStreamsRef.current.delete(id)
      const timer = streamRetryRef.current.get(id)
      if (timer) clearTimeout(timer)
      streamRetryRef.current.delete(id)
      historyVersionsRef.current.set(id, (previous.history_version ?? 0) + 1)
      // 编辑会改写历史，下次读取必须拿完整内容。
      serverCountsRef.current.delete(id)
      eventCursorsRef.current.set(id, editEventStart - 1)
      setSelectedChildren(current => ({ ...current, [id]: '' }))
    }
    requestsRef.current.add(id)
    runningSet.add(id)
    setConversations(current => current.map(item => item.id === id && !item.messageCount
      ? { ...item, title: content.replace(/\s+/g, ' ').slice(0, 60), messageCount: 1 } : item))
    setErrors(current => ({ ...current, [id]: '' }))
    setDrafts(current => ({ ...current, [id]: '' }))
    setStreaming(current => ({ ...current, [id]: '' }))
    streamBaselinesRef.current.set(id, editMessageId ? editIndex + 1 : previous.messages.length)
    setSessions(current => ({ ...current, [id]: {
      ...current[id], run_status: 'queued', run_error: null,
      history_version: editMessageId ? (previous.history_version ?? 0) + 1 : previous.history_version,
      messages: editMessageId ? [...previous.messages.slice(0, editIndex), { ...previous.messages[editIndex], content, turn_event_index: editEventStart }]
        : [...current[id].messages, { role: 'user', content, turn_event_index: current[id].events?.length ?? 0 }],
      events: editMessageId ? (previous.events ?? []).slice(0, editEventStart) : current[id].events,
    } }))
    let completed = false
    let accepted = false
    try {
      await api.chatStream(id, content, delta => {
        setStreaming(current => ({ ...current, [id]: (current[id] ?? '') + delta }))
      }, () => {
        accepted = true
        if (!editMessageId) startEventStream(id)
      }, editMessageId, planEnabled)
      completed = true
      storeSession(await readSession(id))
      setStreaming(current => ({ ...current, [id]: '' }))
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      setErrors(current => ({ ...current, [id]: completed ? `任务已完成，但读取结果失败：${detail}。请刷新会话。` : `请求失败：${detail}` }))
      if (!completed) {
        if (!accepted) setDrafts(current => ({ ...current, [id]: current[id] || content }))
        if (!accepted && editMessageId) {
          // 未确认接收的编辑不能保留临时版本；后续只读查询继续确认真实执行状态。
          historyVersionsRef.current.set(id, previous.history_version ?? 0)
          eventCursorsRef.current.set(id, (previous.events?.length ?? 0) - 1)
          setSessions(current => ({ ...current, [id]: {
            ...current[id], history_version: previous.history_version,
            messages: previous.messages, events: previous.events,
          } }))
        }
        try {
          const latest = await api.getSession(id)
          if (latest.run_status === 'idle') {
            storeSession(latest)
            if (!accepted) setSessions(current => ({ ...current, [id]: { ...current[id], messages: latest.messages } }))
            setStreaming(current => ({ ...current, [id]: '' }))
            if (accepted) setErrors(current => ({ ...current, [id]: '' }))
          } else if (!accepted) {
            storeSession(latest)
            setSessions(current => ({ ...current, [id]: { ...current[id], messages: latest.messages } }))
            startEventStream(id)
          }
          else if ((latest.history_version ?? 0) >= (historyVersionsRef.current.get(id) ?? 0)) {
            storeSession(latest)
            startEventStream(id)
          }
        } catch { /* 状态不明时保留运行标记，避免重复提交。 */ }
      }
    } finally {
      requestsRef.current.delete(id)
      try { await refreshConversations() } catch { setConnectionError('会话列表刷新失败，请稍后重试。') }
      // 首轮完成后服务端在后台生成标题，稍后再刷新一次列表。
      if (completed && firstTurn && !editMessageId) setTimeout(() => { void refreshConversations().catch(() => { /* 下次刷新再取标题。 */ }) }, TITLE_REFRESH_DELAY)
    }
  }

  /** 更新标题并同步会话列表。 */
  async function renameConversation(id: string, title: string) {
    if (!title.trim() || mutatingSet.has(id)) return
    try {
      const detail = await api.renameSession(id, title.trim())
      storeSession(detail)
      setConversations(current => current.map(item => item.id === id ? { ...item, title: detail.title } : item))
      try { await refreshConversations() } catch { setConnectionError('会话已改名，但列表刷新失败，请稍后重试。') }
    } catch (error) {
      setConnectionError(`会话改名失败：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  /** 删除会话后清理本地缓存，并切换到仍存在的会话。 */
  async function deleteConversation(id: string) {
    if (runningSet.has(id) || mutatingSet.has(id) || deletedIdsRef.current.has(id)) return
    mutatingSet.add(id)
    try {
      await api.deleteSession(id)
      deletedIdsRef.current.add(id)
      eventStreamsRef.current.get(id)?.abort()
      eventStreamsRef.current.delete(id)
      const timer = streamRetryRef.current.get(id)
      if (timer) clearTimeout(timer)
      streamRetryRef.current.delete(id)
      for (const cache of [eventCursorsRef.current, historyVersionsRef.current, streamBaselinesRef.current, serverCountsRef.current]) cache.delete(id)
      const without = <T,>(record: Record<string, T>) => { const next = { ...record }; delete next[id]; return next }
      setConversations(current => current.filter(item => item.id !== id))
      setSessions(without)
      setStreaming(without)
      setDrafts(without)
      setErrors(without)
      setSelectedChildren(without)
      setReadReplies(current => id in current ? saveReadReplies(without(current)) : current)
      let remaining = conversations.filter(item => !deletedIdsRef.current.has(item.id))
      try { remaining = await refreshConversations() } catch { setConnectionError('会话已删除，但列表刷新失败，请稍后重试。') }
      if (activeIdRef.current === id) {
        if (remaining.length) await loadConversation(remaining[0].id)
        else await newConversation()
      }
    } catch (error) {
      setConnectionError(`删除会话失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      mutatingSet.remove(id)
    }
  }

  /** 删除最后一轮用户消息，服务端返回的历史版本可覆盖旧缓存。 */
  async function deleteLatestMessage(messageId: string) {
    const id = activeId
    if (!id || runningSet.has(id) || mutatingSet.has(id) || deletedIdsRef.current.has(id)) return
    mutatingSet.add(id)
    try {
      const detail = await api.deleteLatestUserMessage(id, messageId)
      eventStreamsRef.current.get(id)?.abort()
      eventStreamsRef.current.delete(id)
      storeSession(detail)
      setStreaming(current => ({ ...current, [id]: '' }))
      setErrors(current => ({ ...current, [id]: '' }))
      setSelectedChildren(current => ({ ...current, [id]: '' }))
      try { await refreshConversations() } catch { setConnectionError('消息已删除，但列表刷新失败，请稍后重试。') }
    } catch (error) {
      setErrors(current => ({ ...current, [id]: `删除消息失败：${error instanceof Error ? error.message : String(error)}` }))
    } finally {
      mutatingSet.remove(id)
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
      void readSession(card.childSessionId).then(storeSession).catch(() => { /* 选中卡片时可以重试读取。 */ })
        .finally(() => loadingChildrenRef.current.delete(card.childSessionId))
    }
  }, [activeId, activeSession, cards, sessions, readSession, storeSession, startEventStream])

  useEffect(() => {
    if (!selectedChildId) return
    let cancelled = false
    const streams = eventStreamsRef.current
    void readSession(selectedChildId).then(detail => {
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
  }, [activeId, selectedChildId, readSession, storeSession, startEventStream])

  const runningIds = runningSet.ids
  useEffect(() => {
    if (connecting || !runningIds.size) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    /**
     * 每次只请求一次轻量的会话列表确认运行状态；会话详情只在任务结束、
     * 或当前会话没有过程流（断线重连的间隙）时增量读取，不再每秒整份下载。
     */
    async function poll() {
      let listed: SessionSummary[] | null = null
      try { listed = await api.listSessions() } catch { /* 保留运行状态，等下一次轮询确认。 */ }
      if (cancelled) return
      if (listed) {
        const byId = new Map(listed.map(item => [item.id, item]))
        let completed = false
        await Promise.all([...runningIds].map(async id => {
          const summary = byId.get(id)
          const busy = summary && (summary.run_status !== 'idle' || summary.active_subagents)
          if (requestsRef.current.has(id)) {
            if (busy) startEventStream(id)
            return
          }
          if (busy && (id !== activeIdRef.current || eventStreamsRef.current.has(id))) return
          try {
            const session = await readSession(id)
            if (cancelled) return
            const version = session.history_version ?? 0
            if (version < (historyVersionsRef.current.get(id) ?? 0)) return
            storeSession(session)
            if (session.run_status === 'idle' && !session.active_subagents) {
              setStreaming(current => ({ ...current, [id]: '' }))
              setErrors(current => ({ ...current, [id]: '' }))
              completed = true
            }
          } catch { /* 保留运行状态，等后续轮询确认结果。 */ }
        }))
        if (!cancelled && completed) applyListing(listed)
      }
      if (!cancelled) timer = setTimeout(poll, POLL_INTERVAL)
    }
    void poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [api, connecting, runningIds, readSession, storeSession, applyListing, startEventStream])

  const session = activeSession
  const rawMessages = session?.messages, rawEvents = session?.events
  const messages = useMemo(() => normalizeMessages(activeId, rawMessages ?? [], rawEvents), [activeId, rawMessages, rawEvents])
  const timeline = useMemo(() => buildTimeline(session, messages, cards), [session, messages, cards])
  const stats = useMemo(() => sessionStats(session), [session])
  const selectedCard = cards.find(card => card.childSessionId === selectedChildId) ?? null
  const childSession = selectedChildId ? sessions[selectedChildId] ?? null : null
  const childMessages = useMemo(() => normalizeMessages(selectedChildId, childSession?.messages ?? [], childSession?.events),
    [selectedChildId, childSession?.messages, childSession?.events])
  const loading = connecting || loadingSet.ids.has(activeId)
  const running = runningIds.has(activeId)
  const queued = running && session?.run_status === 'queued'
  const error = errors[activeId] || (session?.run_error ? `任务失败：${session.run_error}` : '') || connectionError
  const lastCompression = session?.events?.findLast(event => event?.kind === 'compression')
  const compressing = running && lastCompression?.meta?.state === 'started'
  const contextUsage = session?.context_limit ? {
    label: `${(session.context_tokens ?? 0).toLocaleString('zh-CN')} / ${session.context_limit.toLocaleString('zh-CN')}`,
    percent: Math.round((session.context_tokens ?? 0) / session.context_limit * 1000) / 10,
    description: `上下文用量估算，包含历史消息和已加载的提示词、工具定义。超过 ${(session.compression_threshold ?? 0).toLocaleString('zh-CN')} tokens 时尝试压缩。`,
  } : null
  const compressionNotice = lastCompression ? {
    state: compressing ? 'started' : lastCompression.meta?.state === 'started' ? 'interrupted' : lastCompression.meta?.state,
    message: compressing ? '正在压缩上下文，整理完成后继续回复…'
      : lastCompression.meta?.state === 'completed'
        ? `上下文已压缩：${(lastCompression.meta.before_tokens ?? 0).toLocaleString('zh-CN')} → ${(lastCompression.meta.after_tokens ?? 0).toLocaleString('zh-CN')} tokens`
        : lastCompression.meta?.state === 'started' ? '上次上下文压缩未完成，请检查会话状态。'
          : `${lastCompression.title}：${lastCompression.content ?? ''}`,
  } : null

  const visibleStreaming = (streaming[activeId] ?? '').slice((session?.messages ?? []).slice(streamBaselinesRef.current.get(activeId) ?? session?.messages.length ?? 0)
    .filter(message => message.role === 'assistant' && message.kind !== 'execution_report')
    .reduce((length, message) => length + (typeof message.content === 'string' ? message.content.length : 0), 0))
  const unreadIds = new Set(conversations.filter(item => {
    const detail = sessions[item.id]
    if (runningIds.has(item.id) || detail?.active_subagents || detail?.run_error) return false
    const replyId = detail ? latestReplyId(detail) : item.lastReplyId
    return replyId && !readReplies[item.id]?.includes(replyId)
  }).map(item => item.id))
  const mutatingIds = mutatingSet.ids
  const unreadReplyId = !running && !loading && !mutatingIds.has(activeId) && !session?.active_subagents && !error
    && unreadIds.has(activeId) ? latestReplyId(session) : null
  const activity = getAgentActivity({ session, cards, running, streaming: visibleStreaming, error, loading, creating,
    mutating: mutatingIds.has(activeId), backgroundCount: runningIds.size - Number(running), unread: Boolean(unreadReplyId) })
  const status = running || loading || creating || mutatingIds.has(activeId) ? 'busy' : error ? 'error' : 'ready'

  return {
    planEnabled, compressing, contextUsage, compressionNotice, activity, unreadIds, unreadReplyId, markReplyRead,
    status: status as 'busy' | 'error' | 'ready', stats,
    /** 保存浏览器的执行偏好，下次发送或编辑重跑时使用。 */
    togglePlan: () => setPlanEnabled(current => {
      try { localStorage.setItem('qrclaw.planEnabled', String(!current)) }
      catch { console.warn('无法保存 Plan 模式偏好，本次页面仍可使用。') }
      return !current
    }),
    conversations, activeId, session, messages, timeline, cards, selectedCard, childSession, childMessages,
    childStreaming: childStreaming[selectedChildId] ?? '', selectSubAgent: (id: string | null) =>
      setSelectedChildren(current => ({ ...current, [activeId]: id ?? '' })),
    streaming: visibleStreaming,
    runningIds, running, queued, loading, creating, mutatingIds, mutating: mutatingIds.has(activeId), error,
    draft: drafts[activeId] ?? '',
    setDraft: (value: string) => setDrafts(current => ({ ...current, [activeId]: value })),
    loadConversation, newConversation, renameConversation, deleteConversation, deleteLatestMessage,
    editLatestMessage: (messageId: string, content: string) => send(content, messageId), send,
    refresh: async () => {
      if (activeId) {
        await loadConversation(activeId)
        try { await refreshConversations() } catch { setConnectionError('会话列表刷新失败，请稍后重试。') }
      } else await newConversation()
    },
  }
}
