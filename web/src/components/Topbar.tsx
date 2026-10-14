import { List, PanelRight } from 'lucide-react'
import type { AgentActivity } from '../lib/types'
import { AgentSprite } from './AgentSprite'

type TopbarProps = {
  activity: AgentActivity
  sessionId: string
  title: string
  sidebarOpen: boolean
  inspectorOpen: boolean
  showInspector: boolean
  onToggleSidebar: () => void
  onToggleInspector: () => void
}

/** 展示当前会话标题，以及由 ViewModel 提供的精灵和工作状态。 */
export function Topbar({ activity, sessionId, title, sidebarOpen, inspectorOpen, showInspector, onToggleSidebar, onToggleInspector }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar-heading">
        <button className="icon-button sidebar-toggle" aria-label="切换会话列表" aria-expanded={sidebarOpen} aria-controls="session-sidebar" onClick={onToggleSidebar}><List size={20} /></button>
        <span className="brand">QRClaw</span>
        <span className="conversation-heading" title={title}>{title}</span>
      </div>
      <div className="topbar-actions">
        <div className="agent-status" data-phase={activity.phase}>
          <AgentSprite key={sessionId} phase={activity.phase} />
          <span className={`status-label ${activity.status}`} role="status">{activity.label}</span>
        </div>
        {showInspector && <button className="icon-button" aria-label="切换执行过程" aria-expanded={inspectorOpen} aria-controls="run-inspector" onClick={onToggleInspector}><PanelRight size={19} /></button>}
      </div>
    </header>
  )
}
