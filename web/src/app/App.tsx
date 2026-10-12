import { useEffect, useMemo, useState } from 'react'
import { ChatPanel } from '../features/chat/ChatPanel'
import { useChatWorkspace } from '../features/chat/useChatWorkspace'
import { InspectorPanel } from '../features/inspector/InspectorPanel'
import { Composer } from '../features/composer/Composer'
import { createApiClient } from '../lib/api'
import { Sidebar } from '../components/Sidebar'
import { Topbar } from '../components/Topbar'

export function App() {
  const api = useMemo(() => createApiClient(), [])
  const chat = useChatWorkspace(api)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  useEffect(() => {
    function closePanels(event: KeyboardEvent) {
      if (event.key === 'Escape') { setSidebarOpen(false); setInspectorOpen(false) }
    }
    window.addEventListener('keydown', closePanels)
    return () => window.removeEventListener('keydown', closePanels)
  }, [])
  useEffect(() => {
    const overlay = inspectorOpen && window.matchMedia('(max-width: 1100px)').matches ? 'run-inspector'
      : sidebarOpen && window.matchMedia('(max-width: 760px)').matches ? 'session-sidebar' : ''
    const panel = document.getElementById(overlay)
    if (!panel) return
    const previous = document.activeElement as HTMLElement | null
    const buttons = [...panel.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
    panel.setAttribute('role', 'dialog')
    panel.setAttribute('aria-modal', 'true')
    buttons[0]?.focus()
    function keepFocus(event: KeyboardEvent) {
      if (event.key !== 'Tab' || !buttons.length) return
      const first = buttons[0], last = buttons[buttons.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', keepFocus)
    return () => {
      window.removeEventListener('keydown', keepFocus)
      panel.removeAttribute('role')
      panel.removeAttribute('aria-modal')
      previous?.focus()
    }
  }, [sidebarOpen, inspectorOpen])
  const status = chat.running || chat.loading || chat.creating || chat.mutating ? 'busy' : chat.error ? 'error' : 'ready'
  const statusText = chat.loading ? '载入会话中' : chat.mutating ? '更新会话中' : chat.compressing ? '上下文压缩中' : chat.queued ? '等待执行' : chat.running ? '正在处理任务' : chat.creating ? '新建会话中'
    : chat.error ? '需要检查' : chat.runningIds.size ? `${chat.runningIds.size} 个后台任务处理中` : '可以开始'
  const title = chat.conversations.find(item => item.id === chat.activeId)?.title ?? '新对话'

  return (
    <div className="app-shell">
      <Topbar status={status} statusText={statusText} title={title}
        sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen(value => !value)}
        inspectorOpen={inspectorOpen} onToggleInspector={() => setInspectorOpen(value => !value)} />
      {(sidebarOpen || inspectorOpen) && <button className="panel-backdrop" aria-label="关闭面板" onClick={() => { setSidebarOpen(false); setInspectorOpen(false) }} />}
      <div className={`workspace ${inspectorOpen ? 'with-inspector' : ''}`}>
        <Sidebar conversations={chat.conversations} activeId={chat.activeId} runningIds={chat.runningIds} mutatingIds={chat.mutatingIds}
          open={sidebarOpen} creating={chat.creating || chat.loading && !chat.activeId}
          onNewChat={() => { setSidebarOpen(false); void chat.newConversation() }}
          onSelectConversation={id => { setSidebarOpen(false); void chat.loadConversation(id) }}
          onRenameConversation={(id, title) => void chat.renameConversation(id, title)}
          onDeleteConversation={id => void chat.deleteConversation(id)} />
        <main className="chat-column" id="conversation">
          <ChatPanel sessionId={chat.activeId} timeline={chat.timeline} streaming={chat.streaming} running={chat.running} queued={chat.queued}
            loading={chat.loading} mutating={chat.mutating} onSelectSubAgent={id => { chat.selectSubAgent(id); setInspectorOpen(true) }}
            latestUserMessageId={chat.messages.filter(message => message.role === 'user').at(-1)?.uuid}
            onEditLatestMessage={(id, content) => void chat.editLatestMessage(id, content)}
            onDeleteLatestMessage={id => void chat.deleteLatestMessage(id)} />
          <div className="composer-area">
            {chat.error && <div className="chat-error" role="alert"><span>{chat.error}</span><button onClick={() => void chat.refresh()}>刷新会话</button></div>}
            <Composer value={chat.draft} onChange={chat.setDraft} onSend={chat.send}
              planEnabled={chat.planEnabled} onTogglePlan={chat.togglePlan} contextUsage={chat.contextUsage} compressionNotice={chat.compressionNotice}
              disabled={chat.running || chat.loading || chat.creating || chat.mutating || !chat.session} loading={chat.loading || !chat.session} running={chat.running} />
          </div>
        </main>
        {inspectorOpen && <InspectorPanel session={chat.session} status={status} queued={chat.queued}
          selectedCard={chat.selectedCard} childSession={chat.childSession} childMessages={chat.childMessages}
          childStreaming={chat.childStreaming} onMainAgent={() => chat.selectSubAgent(null)} onClose={() => setInspectorOpen(false)} />}
      </div>
    </div>
  )
}
