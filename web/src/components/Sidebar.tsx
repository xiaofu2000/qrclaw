import { Plus } from 'lucide-react'
import { ConversationList } from '../features/conversations/ConversationList'
import type { Conversation } from '../lib/types'

type SidebarProps = {
  conversations: Conversation[]
  activeId: string
  runningIds: Set<string>
  mutatingIds: Set<string>
  open: boolean
  creating: boolean
  memoryOpen: boolean
  onOpenMemory: () => void
  onNewChat: () => void
  onSelectConversation: (conversationId: string) => void
  onRenameConversation: (conversationId: string, title: string) => void
  onDeleteConversation: (conversationId: string) => void
}

export function Sidebar({ conversations, activeId, runningIds, mutatingIds, open, creating, memoryOpen, onOpenMemory, onNewChat, onSelectConversation, onRenameConversation, onDeleteConversation }: SidebarProps) {
  return (
    <aside id="session-sidebar" className={`sidebar ${open ? 'open' : ''}`} aria-label="历史会话">
      <button className="new-chat" type="button" disabled={creating} onClick={onNewChat}><Plus size={18} />{creating ? '正在创建…' : '新对话'}</button>
      <button className={`memory-nav ${memoryOpen ? 'selected' : ''}`} onClick={onOpenMemory}>记忆</button>
      <div className="sidebar-header">历史会话</div>
      <nav className="sidebar-list" aria-label="选择会话">
        <ConversationList conversations={conversations} activeId={activeId} runningIds={runningIds} mutatingIds={mutatingIds} onSelect={onSelectConversation}
          onRename={onRenameConversation} onDelete={onDeleteConversation} />
      </nav>
      <div className="sidebar-footer">本地工作空间</div>
    </aside>
  )
}
