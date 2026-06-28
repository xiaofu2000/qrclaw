import { ConversationList } from '../features/conversations/ConversationList'
import type { Conversation } from '../lib/types'

type SidebarProps = {
  conversations: Conversation[]
  activeId: string
  onNewChat: () => void
  onSelectConversation: (conversationId: string) => void
}

export function Sidebar({ conversations, activeId, onNewChat, onSelectConversation }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">会话</div>
      <button className="new-chat" type="button" onClick={onNewChat}>
        新建任务
      </button>
      <div className="sidebar-list">
        <ConversationList conversations={conversations} activeId={activeId} onSelect={onSelectConversation} />
      </div>
    </aside>
  )
}
