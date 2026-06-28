import type { Conversation } from '../../lib/types'

type ConversationListProps = {
  conversations: Conversation[]
  activeId: string
  onSelect: (conversationId: string) => void
}

export function ConversationList({ conversations, activeId, onSelect }: ConversationListProps) {
  return (
    <div className="conversation-list">
      {conversations.map((conversation) => (
        <button
          key={conversation.id}
          type="button"
          className={`conversation-item ${conversation.id === activeId ? 'active' : ''}`}
          onClick={() => onSelect(conversation.id)}
        >
          <span className="conversation-title">{conversation.title}</span>
          <span className="conversation-meta">{conversation.updatedAt}</span>
        </button>
      ))}
    </div>
  )
}
