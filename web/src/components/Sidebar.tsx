import { Plus, Library, MessageSquare, HardDrive, X } from 'lucide-react'
import { ConversationList } from '../features/conversations/ConversationList'
import type { Conversation } from '../lib/types'
import type { ReactNode } from 'react'

type SidebarProps = {
  conversations: Conversation[]
  activeId: string
  runningIds: Set<string>
  mutatingIds: Set<string>
  open: boolean
  creating: boolean
  memoryOpen: boolean
  companion: ReactNode
  onOpenMemory: () => void
  onOpenChat: () => void
  onClose: () => void
  onNewChat: () => void
  onSelectConversation: (conversationId: string) => void
  onRenameConversation: (conversationId: string, title: string) => void
  onDeleteConversation: (conversationId: string) => void
}

/** 导航与历史始终在同一位置，数据与会话操作仍由工作区管理。 */
export function Sidebar({ conversations, activeId, runningIds, mutatingIds, open, creating, memoryOpen, companion, onOpenMemory, onOpenChat, onClose, onNewChat, onSelectConversation, onRenameConversation, onDeleteConversation }: SidebarProps) {
  return (
    <aside id="session-sidebar" className={`sidebar ${open ? 'open' : ''}`} aria-label="历史会话">
      <div className="sidebar-brand"><span className="brand-mark" aria-hidden="true">Q<span>·</span></span><div><strong>QRClaw</strong><span>你的 AI 工作伙伴</span></div><button className="icon-button sidebar-close" aria-label="关闭会话列表" onClick={onClose}><X size={20} /></button></div>
      <button className="new-chat" type="button" disabled={creating} onClick={onNewChat}><Plus size={18} />{creating ? '正在创建…' : '开启新对话'}</button>
      <nav className="workspace-nav" aria-label="工作空间导航">
        <button className={`workspace-nav-item ${!memoryOpen ? 'selected' : ''}`} aria-current={!memoryOpen ? 'page' : undefined} onClick={onOpenChat}><MessageSquare size={18} />对话工作台</button>
        <button className={`workspace-nav-item ${memoryOpen ? 'selected' : ''}`} aria-current={memoryOpen ? 'page' : undefined} onClick={onOpenMemory}><Library size={18} />记忆库</button>
      </nav>
      <div className="sidebar-header"><span>最近对话</span><span>{conversations.length}</span></div>
      <nav className="sidebar-list" aria-label="选择会话">
        <ConversationList conversations={conversations} activeId={memoryOpen ? '' : activeId} runningIds={runningIds} mutatingIds={mutatingIds} onSelect={onSelectConversation}
          onRename={onRenameConversation} onDelete={onDeleteConversation} />
      </nav>
      {companion || <div className="sidebar-footer"><HardDrive size={17} /><div><strong>本地工作空间</strong><span>对话与记忆，在这里积累</span></div></div>}
    </aside>
  )
}
