import { Plus, Library, MessageSquare, Inbox, HardDrive, X } from 'lucide-react'
import { ConversationList } from '../features/conversations/ConversationList'
import type { AgentActivity, Conversation } from '../lib/types'
import { AgentSprite } from './AgentSprite'

type SidebarProps = {
  conversations: Conversation[]
  activeId: string
  runningIds: Set<string>
  unreadIds: Set<string>
  mutatingIds: Set<string>
  open: boolean
  creating: boolean
  memoryOpen: boolean
  inboxOpen?: boolean
  inboxCount?: number
  theme?: 'system' | 'light' | 'dark'
  activity: AgentActivity | null
  onOpenMemory: () => void
  onOpenChat: () => void
  onOpenInbox?: () => void
  onCycleTheme?: () => void
  onClose: () => void
  onNewChat: () => void
  onSelectConversation: (conversationId: string) => void
  onRenameConversation: (conversationId: string, title: string) => void
  onDeleteConversation: (conversationId: string) => void
}

const THEME_LABEL = { system: '跟随系统', light: '浅色', dark: '深色' }

/** 导航与历史始终在同一位置；顶部是小精灵本身，状态文字紧挨着它。 */
export function Sidebar({ conversations, activeId, runningIds, unreadIds, mutatingIds, open, creating, memoryOpen, inboxOpen = false, inboxCount = 0,
  theme = 'system', activity, onOpenMemory, onOpenChat, onOpenInbox, onCycleTheme, onClose, onNewChat, onSelectConversation, onRenameConversation, onDeleteConversation }: SidebarProps) {
  const chatOpen = !memoryOpen && !inboxOpen
  return (
    <aside id="session-sidebar" className={`sidebar ${open ? 'open' : ''}`} aria-label="历史会话">
      <div className={`sidebar-brand ${activity ? 'with-companion' : ''}`} data-phase={activity?.phase}>
        {activity ? <div className="sidebar-companion-stage"><AgentSprite key={activeId} phase={activity.phase} /></div>
          : <span className="brand-mark" aria-hidden="true">Q<span>·</span></span>}
        <div className="sidebar-brand-copy"><strong>QRClaw</strong>
          {activity ? <div className={`companion-status ${activity.status}`} role="status"><span className="status-dot" aria-hidden="true" />{activity.label}</div>
            : <span>你的 AI 工作伙伴</span>}
        </div>
        <button className="icon-button sidebar-close" aria-label="关闭会话列表" onClick={onClose}><X size={20} /></button>
      </div>
      <button className="new-chat" type="button" disabled={creating} onClick={onNewChat}><Plus size={18} />{creating ? '正在创建…' : '开启新对话'}</button>
      <nav className="workspace-nav" aria-label="工作空间导航">
        <button className={`workspace-nav-item ${inboxOpen ? 'selected' : ''}`} aria-current={inboxOpen ? 'page' : undefined} onClick={onOpenInbox}>
          <Inbox size={18} />收件箱{inboxCount > 0 && <span className="nav-count">{inboxCount}</span>}</button>
        <button className={`workspace-nav-item ${chatOpen ? 'selected' : ''}`} aria-current={chatOpen ? 'page' : undefined} onClick={onOpenChat}><MessageSquare size={18} />当前对话</button>
        <button className={`workspace-nav-item ${memoryOpen ? 'selected' : ''}`} aria-current={memoryOpen ? 'page' : undefined} onClick={onOpenMemory}><Library size={18} />记忆库</button>
      </nav>
      <div className="sidebar-header"><span>会话</span><span>{conversations.length}</span></div>
      <nav className="sidebar-list" aria-label="选择会话">
        <ConversationList conversations={conversations} activeId={chatOpen ? activeId : ''} runningIds={runningIds} unreadIds={unreadIds} mutatingIds={mutatingIds} onSelect={onSelectConversation}
          onRename={onRenameConversation} onDelete={onDeleteConversation} />
      </nav>
      <div className="sidebar-footer">
        <span><HardDrive size={16} />本地工作空间</span>
        <button className="theme-toggle" type="button" onClick={onCycleTheme} aria-label={`主题：${THEME_LABEL[theme]}，点击切换`}>{THEME_LABEL[theme]}</button>
      </div>
    </aside>
  )
}
