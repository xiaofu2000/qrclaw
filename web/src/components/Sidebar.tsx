import { ConversationList } from '../features/conversations/ConversationList'
import type { Conversation } from '../lib/types'

type SidebarProps = {
  conversations: Conversation[]
  activeId: string
}

export function Sidebar({ conversations, activeId }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">会话</div>
      <button className="new-chat" type="button">
        新建任务
      </button>
      <ConversationList conversations={conversations} activeId={activeId} />
    </aside>
  )
}
