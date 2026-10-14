import { useEffect, useMemo, useState } from 'react'
import { ChatPanel } from '../features/chat/ChatPanel'
import { useChatWorkspace } from '../features/chat/useChatWorkspace'
import { InspectorPanel } from '../features/inspector/InspectorPanel'
import { Composer } from '../features/composer/Composer'
import { createApiClient } from '../lib/api'
import { Sidebar } from '../components/Sidebar'
import { Topbar } from '../components/Topbar'
import { MemoryWorkspace } from '../features/memory/MemoryPanel'

export function App() {
  const api = useMemo(() => createApiClient(), [])
  const chat = useChatWorkspace(api)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(() => window.location.hash.startsWith('#/memory'))
  useEffect(() => {
    /** 刷新和浏览器前进、后退均使用同一个记忆入口。 */
    function navigate() { setMemoryOpen(window.location.hash.startsWith('#/memory')) }
    window.addEventListener('hashchange', navigate)
    return () => window.removeEventListener('hashchange', navigate)
  }, [])
  /** 切回聊天保留 ViewModel 中的草稿和正在执行的任务。 */
  function showChat() { window.location.hash = ''; setMemoryOpen(false) }
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
  const title = chat.conversations.find(item => item.id === chat.activeId)?.title ?? '新对话'

  return (
    <div className="app-shell">
      <Topbar activity={chat.activity} sessionId={chat.activeId} title={memoryOpen ? '记忆' : title}
        sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen(value => !value)}
        inspectorOpen={inspectorOpen} showInspector={!memoryOpen} onToggleInspector={() => setInspectorOpen(value => !value)} />
      {(sidebarOpen || inspectorOpen && !memoryOpen) && <button className="panel-backdrop" aria-label="关闭面板" onClick={() => { setSidebarOpen(false); setInspectorOpen(false) }} />}
      <div className={`workspace ${inspectorOpen && !memoryOpen ? 'with-inspector' : ''}`}>
        <Sidebar conversations={chat.conversations} activeId={chat.activeId} runningIds={chat.runningIds} mutatingIds={chat.mutatingIds}
          open={sidebarOpen} creating={chat.creating || chat.loading && !chat.activeId}
          memoryOpen={memoryOpen} onOpenMemory={() => { window.location.hash = '/memory'; setMemoryOpen(true); setSidebarOpen(false); setInspectorOpen(false) }}
          onNewChat={() => { showChat(); setSidebarOpen(false); void chat.newConversation() }}
          onSelectConversation={id => { showChat(); setSidebarOpen(false); void chat.loadConversation(id) }}
          onRenameConversation={(id, title) => void chat.renameConversation(id, title)}
          onDeleteConversation={id => void chat.deleteConversation(id)} />
        <MemoryWorkspace api={api} visible={memoryOpen} activityKey={chat.runningIds.size}
          onOpenSession={id => { showChat(); void chat.loadConversation(id) }} />
        <main className="chat-column" id="conversation" hidden={memoryOpen}>
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
        {inspectorOpen && !memoryOpen && <InspectorPanel session={chat.session} status={chat.status} queued={chat.queued}
          selectedCard={chat.selectedCard} childSession={chat.childSession} childMessages={chat.childMessages}
          childStreaming={chat.childStreaming} onMainAgent={() => chat.selectSubAgent(null)} onClose={() => setInspectorOpen(false)} />}
      </div>
    </div>
  )
}
