import { List, PanelRight } from 'lucide-react'
import type { RunStatus } from '../lib/types'

type TopbarProps = {
  status: RunStatus
  statusText: string
  title: string
  sidebarOpen: boolean
  inspectorOpen: boolean
  onToggleSidebar: () => void
  onToggleInspector: () => void
}

export function Topbar({ status, statusText, title, sidebarOpen, inspectorOpen, onToggleSidebar, onToggleInspector }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar-heading">
        <button className="icon-button sidebar-toggle" aria-label="切换会话列表" aria-expanded={sidebarOpen} aria-controls="session-sidebar" onClick={onToggleSidebar}><List size={20} /></button>
        <span className="brand">QRClaw</span>
        <span className="conversation-heading" title={title}>{title}</span>
      </div>
      <div className="topbar-actions">
        <span className={`status-label ${status}`} role="status">{statusText}</span>
        <button className="icon-button" aria-label="切换执行过程" aria-expanded={inspectorOpen} aria-controls="run-inspector" onClick={onToggleInspector}><PanelRight size={19} /></button>
      </div>
    </header>
  )
}
