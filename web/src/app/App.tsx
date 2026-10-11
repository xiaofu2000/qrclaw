import { useEffect, useMemo, useRef, useState } from 'react'
import { ChatPanel } from '../features/chat/ChatPanel'
import { InspectorPanel } from '../features/inspector/InspectorPanel'
import { Composer } from '../features/composer/Composer'
import { createApiClient } from '../lib/api'
import type { Conversation, Message, RunStatus, SessionDetail, SessionSummary } from '../lib/types'
import { Sidebar } from '../components/Sidebar'
import { Topbar } from '../components/Topbar'

function toConversation(session: SessionSummary): Conversation {
  return { id: session.id, title: session.title || session.id, updatedAt: session.updated_at, messageCount: session.message_count }
}

function normalizeMessages(session: SessionDetail): Message[] {
  const toolNames = new Map<string, string>()
  return session.messages.flatMap((message, index) => {
    for (const call of message.tool_calls ?? []) {
      toolNames.set(call.id, call.function?.name ?? '工具')
    }
    if (message.role === 'assistant' && message.tool_calls?.length && !message.content) return []
    return [{
      id: `${session.id}-${index}`,
      role: message.role as Message['role'],
      kind: message.kind,
      content: typeof message.content === 'string' ? message.content : JSON.stringify(message.content),
      toolName: message.tool_call_id ? toolNames.get(message.tool_call_id) : undefined,
    }]
  })
}

export function App() {
  const api = useMemo(() => createApiClient(), [])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string>('')
  const activeIdRef = useRef('')
  const [messages, setMessages] = useState<Message[]>([])
  const [activeSession, setActiveSession] = useState<SessionDetail | null>(null)
  const [status, setStatus] = useState<RunStatus>('ready')
  const [statusText, setStatusText] = useState('已连接本地服务')

  useEffect(() => {
    let cancelled = false
    async function bootstrap() {
      try {
        await api.health()
        const sessions = await api.listSessions()
        if (cancelled) {
          return
        }
        setConversations(sessions.map(toConversation))
        if (sessions.length > 0) {
          const first = sessions[0]
          activeIdRef.current = first.id
          setActiveConversationId(first.id)
          const detail = await api.getSession(first.id)
          if (!cancelled && activeIdRef.current === first.id) {
            setActiveSession(detail)
            setMessages(normalizeMessages(detail))
          }
        } else {
          const created = await api.createSession()
          if (!cancelled) {
            activeIdRef.current = created.id
            setConversations([
              {
                id: created.id,
                title: created.id,
                updatedAt: created.updated_at,
                messageCount: created.message_count,
              },
            ])
            setActiveConversationId(created.id)
            setActiveSession(created)
            setMessages([])
          }
        }
      } catch {
        if (!cancelled) {
          setStatus('error')
          setStatusText('本地服务未连接')
        }
      }
    }

    bootstrap()
    return () => {
      cancelled = true
    }
  }, [api])

  async function refreshConversations() {
    const sessions = await api.listSessions()
    setConversations(sessions.map(toConversation))
  }

  async function loadConversation(conversationId: string) {
    activeIdRef.current = conversationId
    const detail = await api.getSession(conversationId)
    if (activeIdRef.current !== conversationId) return
    setActiveConversationId(conversationId)
    setActiveSession(detail)
    setMessages(normalizeMessages(detail))
  }

  async function handleNewChat() {
    setStatus('busy')
    setStatusText('新建会话中')
    try {
      const created = await api.createSession()
      await refreshConversations()
      activeIdRef.current = created.id
      setActiveConversationId(created.id)
      setActiveSession(created)
      setMessages([])
      setStatus('ready')
      setStatusText('已创建新会话')
    } catch (error) {
      setStatus('error')
      setStatusText(error instanceof Error ? error.message : '新建会话失败')
    }
  }

  async function handleSend(content: string) {
    if (!activeConversationId) {
      return
    }
    const sessionId = activeConversationId
    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
    }
    setMessages((current) => [...current, userMessage])
    setStatus('busy')
    setStatusText('运行中')

    try {
      const nextMessages = [...messages, userMessage]
      await api.chat(sessionId, nextMessages)
      const updated = await api.getSession(sessionId)
      if (activeIdRef.current === sessionId) {
        setActiveSession(updated)
        setMessages(normalizeMessages(updated))
      }
      await refreshConversations()
      setStatus('ready')
      setStatusText('完成')
    } catch (error) {
      if (activeIdRef.current === sessionId) {
        setMessages((current) => [...current, {
          id: crypto.randomUUID(),
          role: 'assistant',
          kind: 'assistant',
          content: `请求失败：${error instanceof Error ? error.message : String(error)}`,
        }])
      }
      setStatus('error')
      setStatusText('调用失败')
    }
  }

  return (
    <div className="app-shell">
      <Topbar status={status} statusText={statusText} />
      <div className="workspace">
        <Sidebar
          conversations={conversations}
          activeId={activeConversationId}
          onNewChat={handleNewChat}
          onSelectConversation={loadConversation}
        />
        <main className="chat-column">
          <div className="chat-stage">
            <ChatPanel messages={messages} />
          </div>
          <Composer onSend={handleSend} disabled={status === 'busy'} />
        </main>
        <InspectorPanel session={activeSession} status={status} />
      </div>
    </div>
  )
}
