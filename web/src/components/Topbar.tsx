import { Menu, PanelRight, MessageSquare, Library } from 'lucide-react'
import type { ReactNode } from 'react'
import type { AgentActivity } from '../lib/types'

type TopbarProps = {
  title: string
  memoryOpen: boolean
  companion: ReactNode
  activity: AgentActivity | null
  sidebarOpen: boolean
  inspectorOpen: boolean
  onToggleSidebar: () => void
  onToggleInspector: () => void
}

/** 状态文案留在顶部；窄屏继续将伙伴与状态合在顶栏。 */
export function Topbar({ title, memoryOpen, companion, activity, sidebarOpen, inspectorOpen, onToggleSidebar, onToggleInspector }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar-heading">
        <button className="icon-button sidebar-toggle" aria-label="切换会话列表" aria-expanded={sidebarOpen} aria-controls="session-sidebar" onClick={onToggleSidebar}><Menu size={20} /></button>
        <span className="page-symbol" aria-hidden="true">{memoryOpen ? <Library size={18} /> : <MessageSquare size={18} />}</span>
        <span className="page-kind">{memoryOpen ? '记忆库' : '对话'}</span>
        <span className="heading-divider" aria-hidden="true">/</span>
        <span className="conversation-heading" title={title}>{memoryOpen ? '让经验成为下一次的起点' : title}</span>
      </div>
      {companion}
      {activity && <div className={`companion-status topbar-status ${activity.status}`} role="status"><span className="status-dot" aria-hidden="true" />{activity.label}</div>}
      {!memoryOpen && <button className="inspector-toggle" aria-label="切换执行过程" aria-expanded={inspectorOpen} aria-controls="run-inspector" onClick={onToggleInspector}>
        <PanelRight size={17} /><span>执行过程</span>
      </button>}
    </header>
  )
}
