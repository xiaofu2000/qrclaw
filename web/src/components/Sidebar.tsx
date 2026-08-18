import { ConversationList } from '../features/conversations/ConversationList'
import type { Conversation } from '../models/workbench'

type SidebarProps = {
  conversations: Conversation[]
  activeId: string | null
  disabled: boolean
  onNewConversation: () => void
  onSelectConversation: (id: string) => void
  onRenameConversation: (id: string, title: string) => void
  onDeleteConversation: (id: string) => void
}

/** 左侧会话与工作区导航。 */
export function Sidebar({ conversations, activeId, disabled, onNewConversation, onSelectConversation, onRenameConversation, onDeleteConversation }: SidebarProps) {
  return (
    <aside className="sidebar">
      <button className="new-chat" type="button" onClick={onNewConversation} disabled={disabled}>＋ 新建任务</button>
      <div className="sidebar-section-label">会话</div>
      <ConversationList conversations={conversations} activeId={activeId} disabled={disabled} onSelect={onSelectConversation} onRename={onRenameConversation} onDelete={onDeleteConversation} />
      <div className="sidebar-section-label workspace-label">工作区</div>
      <nav className="workspace-navigation" aria-label="后续工作区功能">
        <span>Agent <small>查看运行详情</small></span>
        <span className="muted">Wiki 记忆 <small>P1</small></span>
        <span className="muted">Skills <small>P1</small></span>
      </nav>
    </aside>
  )
}
