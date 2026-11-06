import { CircleDashed, CircleDot, LoaderCircle } from 'lucide-react'
import type { Conversation } from '../../lib/types'
import { SessionState } from '../conversations/ConversationList'

type InboxViewProps = {
  conversations: Conversation[]
  activeId: string
  runningIds: Set<string>
  unreadIds: Set<string>
  onOpen: (conversationId: string) => void
}

/** 收件箱：把会话按运行中、等你查看、其他分组，只用会话列表接口已有的字段。 */
export function InboxView({ conversations, runningIds, unreadIds, onOpen }: InboxViewProps) {
  const running = conversations.filter(item => runningIds.has(item.id))
  const unread = conversations.filter(item => !runningIds.has(item.id) && unreadIds.has(item.id))
  const rest = conversations.filter(item => !runningIds.has(item.id) && !unreadIds.has(item.id))
  const row = (item: Conversation) => <button key={item.id} type="button" className="inbox-row" onClick={() => onOpen(item.id)}>
    <SessionState running={runningIds.has(item.id)} unread={unreadIds.has(item.id)} empty={!item.messageCount} />
    <span className="conversation-title">{item.title}</span>
    <span className="inbox-meta"><span>{item.messageCount ? `${item.messageCount} 条消息` : '空白'}</span><span>{item.updatedAt}</span></span>
  </button>
  return (
    <section className="inbox-view" aria-label="收件箱">
      <div className="inbox-inner">
        <h1>收件箱</h1>
        <p>运行中的任务和还没看的回复都在这里。</p>
        <div className="inbox-group"><LoaderCircle size={15} aria-hidden="true" />运行中 · {running.length}</div>
        {running.length ? running.map(row) : <div className="inbox-empty">没有正在执行的任务。</div>}
        <div className="inbox-group"><CircleDot size={15} aria-hidden="true" />等你查看 · {unread.length}</div>
        {unread.length ? unread.map(row) : <div className="inbox-empty">回复都看过了。</div>}
        <div className="inbox-group"><CircleDashed size={15} aria-hidden="true" />其他会话 · {rest.length}</div>
        {rest.map(row)}
      </div>
    </section>
  )
}
