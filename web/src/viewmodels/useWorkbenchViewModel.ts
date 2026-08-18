import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  Agent,
  ConnectionState,
  Conversation,
  Message,
  RunSnapshot,
  Settings,
  SettingsInput,
} from '../models/workbench'
import { isRunActive } from '../models/workbench'
import { RunRepository } from '../repositories/runRepository'
import {
  readAccessToken,
  saveAccessToken,
  WorkbenchRepository,
} from '../repositories/workbenchRepository'
import { ApiError, ApiService } from '../services/apiService'
import { EventService } from '../services/eventService'

export type CreateConversationInput = {
  title: string
  workspacePath: string
}

export type WorkbenchViewModel = {
  loading: boolean
  operationPending: boolean
  conversations: Conversation[]
  activeConversation: Conversation | null
  messages: Message[]
  snapshot: RunSnapshot | null
  connection: ConnectionState
  error: string | null
  authRequired: boolean
  settings: Settings | null
  settingsOpen: boolean
  createConversationOpen: boolean
  selectedAgent: Agent | null
  canSend: boolean
  canCancel: boolean
  selectConversation: (id: string) => Promise<void>
  createConversation: (input: CreateConversationInput) => Promise<void>
  renameConversation: (id: string, title: string) => Promise<void>
  deleteConversation: (id: string) => Promise<void>
  sendMessage: (content: string) => Promise<void>
  cancelRun: () => Promise<void>
  resolveApproval: (approvalId: string, decision: 'allow_once' | 'deny') => Promise<void>
  retry: () => Promise<void>
  configureAccessToken: (token: string) => void
  saveSettings: (input: SettingsInput) => Promise<void>
  testModelConnection: () => Promise<string>
  setSettingsOpen: (open: boolean) => void
  setCreateConversationOpen: (open: boolean) => void
  selectAgent: (id: string | null) => void
}

/** 创建与部署方式无关的 WebSocket 地址。 */
function eventUrl(): string {
  const configured = import.meta.env.VITE_QRCLAW_EVENTS_URL
  if (configured) return configured
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/api/v1/events`
}

/** 合并历史消息与当前运行的流式助手消息。 */
function mergeMessages(history: Message[], snapshot: RunSnapshot | null): Message[] {
  if (!snapshot) return history
  const ids = new Set(history.map((item) => item.id))
  const streamed = snapshot.messages
    .filter((item) => !ids.has(item.id))
    .map<Message>((item) => ({ id: item.id, role: 'assistant', content: item.content }))
  return [...history, ...streamed]
}

/**
 * 工作台页面 ViewModel。
 * 负责协调会话、运行、授权、恢复和设置，组件本身不访问网络层。
 */
export function useWorkbenchViewModel(): WorkbenchViewModel {
  const [accessToken, setAccessToken] = useState(readAccessToken)
  const [loading, setLoading] = useState(true)
  const [operationPending, setOperationPending] = useState(false)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [historyMessages, setHistoryMessages] = useState<Message[]>([])
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null)
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [error, setError] = useState<string | null>(null)
  const [authRequired, setAuthRequired] = useState(false)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [createConversationOpen, setCreateConversationOpen] = useState(false)
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const unsubscribeRef = useRef<(() => void) | null>(null)

  const dependencies = useMemo(() => {
    const api = new ApiService(import.meta.env.VITE_QRCLAW_API_BASE_URL || '/api/v1', accessToken)
    return {
      api,
      workbench: new WorkbenchRepository(api),
      runs: new RunRepository(api, new EventService(eventUrl(), accessToken)),
    }
  }, [accessToken])

  const reportError = useCallback((reason: unknown) => {
    const message = reason instanceof Error ? reason.message : String(reason)
    setError(message)
    if (reason instanceof ApiError && reason.code === 'invalid_access_token') {
      setAuthRequired(true)
      setSettingsOpen(true)
    }
  }, [])

  const connectRun = useCallback(
    (run: RunSnapshot) => {
      unsubscribeRef.current?.()
      setSnapshot(run)
      if (!isRunActive(run.run.status)) {
        setConnection('connected')
        return
      }
      unsubscribeRef.current = dependencies.runs.subscribe(
        run.run.id,
        (next) => {
          setSnapshot(next)
          if (!isRunActive(next.run.status)) {
            setConnection('connected')
          }
        },
        setConnection,
        setError,
      )
    },
    [dependencies.runs],
  )

  const loadConversation = useCallback(
    async (id: string) => {
      unsubscribeRef.current?.()
      unsubscribeRef.current = null
      setActiveConversationId(id)
      dependencies.workbench.setActiveConversationId(id)
      setSelectedAgentId(null)
      setSnapshot(null)
      setConnection('connected')
      const messages = await dependencies.workbench.getMessages(id)
      setHistoryMessages(messages)
      const runId = dependencies.workbench.getRunId(id)
      if (!runId) return
      try {
        connectRun(await dependencies.runs.load(runId))
      } catch (reason) {
        if (!(reason instanceof ApiError && reason.code === 'run_not_found')) throw reason
      }
    },
    [connectRun, dependencies.runs, dependencies.workbench],
  )

  const bootstrap = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      await dependencies.api.health()
      if (!accessToken) {
        setAuthRequired(true)
        setSettingsOpen(true)
        setConnection('connected')
        return
      }
      const [, allConversations, loadedSettings] = await Promise.all([
        dependencies.api.capabilities(),
        dependencies.workbench.listConversations(),
        dependencies.workbench.getSettings(),
      ])
      setAuthRequired(false)
      setConversations(allConversations)
      setSettings(loadedSettings)
      const remembered = dependencies.workbench.getActiveConversationId()
      const active = allConversations.find((item) => item.id === remembered) ?? allConversations[0]
      if (active) await loadConversation(active.id)
      else setCreateConversationOpen(true)
    } catch (reason) {
      setConnection('error')
      reportError(reason)
    } finally {
      setLoading(false)
    }
  }, [accessToken, dependencies.api, dependencies.workbench, loadConversation, reportError])

  useEffect(() => {
    void bootstrap()
    return () => unsubscribeRef.current?.()
  }, [bootstrap])

  const selectConversation = useCallback(
    async (id: string) => {
      setOperationPending(true)
      setError(null)
      try {
        await loadConversation(id)
      } catch (reason) {
        reportError(reason)
      } finally {
        setOperationPending(false)
      }
    },
    [loadConversation, reportError],
  )

  const createConversation = useCallback(
    async (input: CreateConversationInput) => {
      setOperationPending(true)
      setError(null)
      try {
        const created = await dependencies.workbench.createConversation(
          input.title,
          input.workspacePath,
        )
        const next = [created, ...conversations]
        setConversations(next)
        setCreateConversationOpen(false)
        await loadConversation(created.id)
      } catch (reason) {
        reportError(reason)
      } finally {
        setOperationPending(false)
      }
    },
    [conversations, dependencies.workbench, loadConversation, reportError],
  )

  const renameConversation = useCallback(
    async (id: string, title: string) => {
      if (!title.trim()) return
      try {
        const updated = await dependencies.workbench.renameConversation(id, title.trim())
        setConversations((items) => items.map((item) => (item.id === id ? updated : item)))
      } catch (reason) {
        reportError(reason)
      }
    },
    [dependencies.workbench, reportError],
  )

  const deleteConversation = useCallback(
    async (id: string) => {
      setOperationPending(true)
      try {
        await dependencies.workbench.deleteConversation(id)
        const next = conversations.filter((item) => item.id !== id)
        setConversations(next)
        if (activeConversationId === id) {
          if (next[0]) await loadConversation(next[0].id)
          else {
            setActiveConversationId(null)
            setHistoryMessages([])
            setSnapshot(null)
            setCreateConversationOpen(true)
          }
        }
      } catch (reason) {
        reportError(reason)
      } finally {
        setOperationPending(false)
      }
    },
    [activeConversationId, conversations, dependencies.workbench, loadConversation, reportError],
  )

  const sendMessage = useCallback(
    async (content: string) => {
      if (!activeConversationId || isRunActive(snapshot?.run.status)) return
      const optimistic: Message = {
        id: `local_${crypto.randomUUID()}`,
        role: 'user',
        content,
      }
      setHistoryMessages((items) => [...items, optimistic])
      setOperationPending(true)
      setError(null)
      try {
        const run = await dependencies.runs.start(activeConversationId, content)
        dependencies.workbench.setRunId(activeConversationId, run.run.id)
        connectRun(run)
        setConversations(await dependencies.workbench.listConversations())
      } catch (reason) {
        setHistoryMessages((items) => items.filter((item) => item.id !== optimistic.id))
        reportError(reason)
      } finally {
        setOperationPending(false)
      }
    },
    [activeConversationId, connectRun, dependencies.runs, dependencies.workbench, reportError, snapshot],
  )

  const cancelRun = useCallback(async () => {
    setOperationPending(true)
    try {
      await dependencies.runs.cancel()
    } catch (reason) {
      reportError(reason)
    } finally {
      setOperationPending(false)
    }
  }, [dependencies.runs, reportError])

  const resolveApproval = useCallback(
    async (approvalId: string, decision: 'allow_once' | 'deny') => {
      setOperationPending(true)
      try {
        await dependencies.runs.resolveApproval(approvalId, decision)
      } catch (reason) {
        reportError(reason)
      } finally {
        setOperationPending(false)
      }
    },
    [dependencies.runs, reportError],
  )

  const configureAccessToken = useCallback((token: string) => {
    const normalized = token.trim()
    saveAccessToken(normalized)
    setAccessToken(normalized)
    setAuthRequired(false)
    setSettingsOpen(false)
  }, [])

  const saveSettings = useCallback(
    async (input: SettingsInput) => {
      setOperationPending(true)
      try {
        setSettings(await dependencies.workbench.updateSettings(input))
        setSettingsOpen(false)
      } catch (reason) {
        reportError(reason)
      } finally {
        setOperationPending(false)
      }
    },
    [dependencies.workbench, reportError],
  )

  const testModelConnection = useCallback(async () => {
    try {
      return await dependencies.workbench.testConnection()
    } catch (reason) {
      reportError(reason)
      throw reason
    }
  }, [dependencies.workbench, reportError])

  const activeConversation =
    conversations.find((item) => item.id === activeConversationId) ?? null
  const selectedAgent = snapshot?.agents.find((item) => item.id === selectedAgentId) ?? null

  return {
    loading,
    operationPending,
    conversations,
    activeConversation,
    messages: mergeMessages(historyMessages, snapshot),
    snapshot,
    connection,
    error,
    authRequired,
    settings,
    settingsOpen,
    createConversationOpen,
    selectedAgent,
    canSend: Boolean(activeConversationId) && !operationPending && !isRunActive(snapshot?.run.status),
    canCancel: isRunActive(snapshot?.run.status) && snapshot?.run.status !== 'cancelling',
    selectConversation,
    createConversation,
    renameConversation,
    deleteConversation,
    sendMessage,
    cancelRun,
    resolveApproval,
    retry: bootstrap,
    configureAccessToken,
    saveSettings,
    testModelConnection,
    setSettingsOpen,
    setCreateConversationOpen,
    selectAgent: setSelectedAgentId,
  }
}

