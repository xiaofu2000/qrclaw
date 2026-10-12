import { LoaderCircle } from 'lucide-react'
import type { Conversation } from '../../lib/types'

type ConversationListProps = {
  conversations: Conversation[]
  activeId: string
  runningIds: Set<string>
  onSelect: (conversationId: string) => void
}

export function ConversationList({ conversations, activeId, runningIds, onSelect }: ConversationListProps) {
  return (
    <div className="conversation-list">
      {conversations.map((conversation) => (
        <button key={conversation.id} type="button" title={conversation.title}
          aria-current={conversation.id === activeId ? 'page' : undefined}
          className={`conversation-item ${conversation.id === activeId ? 'active' : ''}`}
          onClick={() => onSelect(conversation.id)}>
          <span className="conversation-title">{conversation.messageCount === 0 ? '新对话' : conversation.title}</span>
          {runningIds.has(conversation.id) && <LoaderCircle size={15} className="spin" aria-label="任务处理中" />}
        </button>
      ))}
    </div>
  )
}
