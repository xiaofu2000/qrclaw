import { useEffect, useMemo, useState } from 'react'
import { ChatPanel } from '../features/chat/ChatPanel'
import { InspectorPanel } from '../features/inspector/InspectorPanel'
import { Composer } from '../features/composer/Composer'
import { createApiClient } from '../lib/api'
import type { Conversation, Message, RunStatus } from '../lib/types'
import { Sidebar } from '../components/Sidebar'
import { Topbar } from '../components/Topbar'

export function App() {
  const api = useMemo(() => createApiClient(), [])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string>('')
  const [messages, setMessages] = useState<Message[]>([])
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
        const mapped = sessions.map((session) => ({
          id: session.id,
          title: session.id,
          updatedAt: session.updated_at,
          messageCount: session.message_count,
        }))
        setConversations(mapped)
        if (sessions.length > 0) {
          const first = sessions[0]
          setActiveConversationId(first.id)
          const detail = await api.getSession(first.id)
          if (!cancelled) {
            setMessages(
              detail.messages.map((message, index) => ({
                id: `${first.id}-${index}`,
                role: message.role as Message['role'],
                content: typeof message.content === 'string' ? message.content : JSON.stringify(message.content),
              })),
            )
          }
        } else {
          const created = await api.createSession()
          if (!cancelled) {
            setConversations([
              {
                id: created.id,
                title: created.id,
                updatedAt: created.updated_at,
                messageCount: created.message_count,
              },
            ])
            setActiveConversationId(created.id)
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
    setConversations(
      sessions.map((session) => ({
        id: session.id,
        title: session.id,
        updatedAt: session.updated_at,
        messageCount: session.message_count,
      })),
    )
  }

  async function loadConversation(conversationId: string) {
    const detail = await api.getSession(conversationId)
    setActiveConversationId(conversationId)
    setMessages(
      detail.messages.map((message, index) => ({
        id: `${conversationId}-${index}`,
        role: message.role as Message['role'],
        content: typeof message.content === 'string' ? message.content : JSON.stringify(message.content),
      })),
    )
  }

  async function handleNewChat() {
    setStatus('busy')
    setStatusText('新建会话中')
    try {
      const created = await api.createSession()
      await refreshConversations()
      setActiveConversationId(created.id)
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
      const reply = await api.chat(activeConversationId, nextMessages)
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: reply,
        },
      ])
      await refreshConversations()
      setStatus('ready')
      setStatusText('完成')
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: `请求失败：${error instanceof Error ? error.message : String(error)}`,
        },
      ])
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
          <ChatPanel messages={messages} />
          <Composer onSend={handleSend} disabled={status === 'busy'} />
        </main>
        <InspectorPanel />
      </div>
    </div>
  )
}
