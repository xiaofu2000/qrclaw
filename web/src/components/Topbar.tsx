import { Menu, PanelRight, MessageSquare, Library, Inbox } from 'lucide-react'
import type { ReactNode } from 'react'

type TopbarProps = {
  title: string
  memoryOpen: boolean
  inboxOpen?: boolean
  companion: ReactNode
  sidebarOpen: boolean
  inspectorOpen: boolean
  onToggleSidebar: () => void
  onToggleInspector: () => void
}

/** 桌面顶栏呈现页面位置；窄屏同时容纳伙伴、状态与属性抽屉入口。 */
export function Topbar({ title, memoryOpen, inboxOpen = false, companion, sidebarOpen, inspectorOpen, onToggleSidebar, onToggleInspector }: TopbarProps) {
  const kind = memoryOpen ? '记忆库' : inboxOpen ? '收件箱' : '会话'
  return (
    <header className="topbar">
      <div className="topbar-heading">
        <button className="icon-button sidebar-toggle" aria-label="切换会话列表" aria-expanded={sidebarOpen} aria-controls="session-sidebar" onClick={onToggleSidebar}><Menu size={20} /></button>
        <span className="page-symbol" aria-hidden="true">{memoryOpen ? <Library size={18} /> : inboxOpen ? <Inbox size={18} /> : <MessageSquare size={18} />}</span>
        <span className="page-kind">{kind}</span>
        <span className="heading-divider" aria-hidden="true">›</span>
        <span className="conversation-heading" title={title}>{memoryOpen ? '让经验成为下一次的起点' : inboxOpen ? '运行中与等你查看的会话' : title}</span>
      </div>
      {companion}
      {!memoryOpen && !inboxOpen && <button className="inspector-toggle" aria-label="切换会话属性" aria-expanded={inspectorOpen} aria-controls="run-inspector" onClick={onToggleInspector}>
        <PanelRight size={17} /><span>属性</span>
      </button>}
    </header>
  )
}
