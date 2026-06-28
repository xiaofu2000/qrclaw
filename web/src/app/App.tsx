import { useEffect, useMemo, useState } from 'react'
import { ChatPanel } from '../features/chat/ChatPanel'
import { InspectorPanel } from '../features/inspector/InspectorPanel'
import { Composer } from '../features/composer/Composer'
import { createApiClient } from '../lib/api'
import type { Conversation, Message, RunStatus } from '../lib/types'
import { Sidebar } from '../components/Sidebar'
import { Topbar } from '../components/Topbar'

const seedConversations: Conversation[] = [
  { id: 'default', title: '默认会话', updatedAt: '刚刚' },
  { id: 'architecture', title: '架构止血', updatedAt: '今天' },
]

const seedMessages: Message[] = [
  { id: 'm1', role: 'assistant', content: '我会把任务拆成可执行步骤。' },
]

export function App() {
  const api = useMemo(() => createApiClient(), [])
  const [conversations] = useState(seedConversations)
  const [activeConversationId] = useState('default')
  const [messages, setMessages] = useState<Message[]>(seedMessages)
  const [status, setStatus] = useState<RunStatus>('ready')
  const [statusText, setStatusText] = useState('已连接本地服务')

  useEffect(() => {
    api.health().catch(() => {
      setStatus('error')
      setStatusText('本地服务未连接')
    })
  }, [api])

  async function handleSend(content: string) {
    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
    }
    setMessages((current) => [...current, userMessage])
    setStatus('busy')
    setStatusText('运行中')

    try {
      const reply = await api.chat(messages.concat(userMessage))
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: reply,
        },
      ])
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
        <Sidebar conversations={conversations} activeId={activeConversationId} />
        <main className="chat-column">
          <ChatPanel messages={messages} />
          <Composer onSend={handleSend} disabled={status === 'busy'} />
        </main>
        <InspectorPanel />
      </div>
    </div>
  )
}
