import type { Conversation } from '../../lib/types'

type ConversationListProps = {
  conversations: Conversation[]
  activeId: string
}

export function ConversationList({ conversations, activeId }: ConversationListProps) {
  return (
    <div className="conversation-list">
      {conversations.map((conversation) => (
        <button
          key={conversation.id}
          type="button"
          className={`conversation-item ${conversation.id === activeId ? 'active' : ''}`}
        >
          <span className="conversation-title">{conversation.title}</span>
          <span className="conversation-meta">{conversation.updatedAt}</span>
        </button>
      ))}
    </div>
  )
}
