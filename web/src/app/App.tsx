import { useEffect, useMemo, useState } from 'react'
import { ChatPanel } from '../features/chat/ChatPanel'
import { useChatWorkspace } from '../features/chat/useChatWorkspace'
import { InspectorPanel } from '../features/inspector/InspectorPanel'
import { Composer } from '../features/composer/Composer'
import { createApiClient } from '../lib/api'
import { Sidebar } from '../components/Sidebar'
import { Topbar } from '../components/Topbar'
import { MemoryWorkspace } from '../features/memory/MemoryPanel'
import { AgentCompanion } from '../components/AgentCompanion'
import { ArrowUpRight, Code2, ListTodo, BookOpen } from 'lucide-react'

/** 组合工作区视图，业务数据与任务状态仍由现有 ViewModel 提供。 */
export function App() {
  const api = useMemo(() => createApiClient(), [])
  const chat = useChatWorkspace(api)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(() => window.location.hash.startsWith('#/memory'))
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 760px)').matches)
  useEffect(() => {
    // 只挂载一个伙伴实例，窄屏收进顶栏，桌面留在导航侧。
    const media = window.matchMedia('(max-width: 760px)')
    function resize() { setNarrow(media.matches) }
    media.addEventListener('change', resize)
    return () => media.removeEventListener('change', resize)
  }, [])
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
    const overlay = inspectorOpen && !memoryOpen && window.matchMedia('(max-width: 1100px)').matches ? 'run-inspector'
      : sidebarOpen && window.matchMedia('(max-width: 760px)').matches ? 'session-sidebar' : ''
    const panel = document.getElementById(overlay)
    if (!panel) return
    const previous = document.activeElement as HTMLElement | null
    /** 包含改名输入等动态出现的可聚焦控件。 */
    function focusable() {
      return [...panel!.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], summary, [tabindex="0"]')]
        .filter(element => element.getClientRects().length > 0)
    }
    panel.setAttribute('role', 'dialog')
    panel.setAttribute('aria-modal', 'true')
    focusable()[0]?.focus()
    function keepFocus(event: KeyboardEvent) {
      const buttons = focusable()
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
  }, [sidebarOpen, inspectorOpen, memoryOpen])
  const title = chat.conversations.find(item => item.id === chat.activeId)?.title ?? '新对话'
  const empty = !chat.timeline.length && !chat.running && !chat.loading
  const companion = !empty || memoryOpen ? <AgentCompanion activity={chat.activity} sessionId={chat.activeId} compact /> : null

  /** 快捷入口仅填写草稿，仍由用户确认并发送。 */
  function choosePrompt(prompt: string) {
    chat.setDraft(prompt)
    document.getElementById('message-input')?.focus()
  }

  return (
    <div className="app-shell">
      {(sidebarOpen || inspectorOpen && !memoryOpen) && <button className="panel-backdrop" aria-label="关闭面板" onClick={() => { setSidebarOpen(false); setInspectorOpen(false) }} />}
        <Sidebar conversations={chat.conversations} activeId={chat.activeId} runningIds={chat.runningIds} mutatingIds={chat.mutatingIds}
          open={sidebarOpen} creating={chat.creating || chat.loading && !chat.activeId} companion={!narrow ? companion : null}
          onOpenChat={() => { showChat(); setSidebarOpen(false) }} onClose={() => setSidebarOpen(false)}
          memoryOpen={memoryOpen} onOpenMemory={() => { window.location.hash = '/memory'; setMemoryOpen(true); setSidebarOpen(false); setInspectorOpen(false) }}
          onNewChat={() => { showChat(); setSidebarOpen(false); void chat.newConversation() }}
          onSelectConversation={id => { showChat(); setSidebarOpen(false); void chat.loadConversation(id) }}
          onRenameConversation={(id, title) => void chat.renameConversation(id, title)}
          onDeleteConversation={id => void chat.deleteConversation(id)} />
      <div className="work-surface">
        <Topbar title={title} memoryOpen={memoryOpen} companion={narrow ? companion : null}
          sidebarOpen={sidebarOpen} onToggleSidebar={() => { setSidebarOpen(value => !value); setInspectorOpen(false) }}
          inspectorOpen={inspectorOpen} onToggleInspector={() => { setInspectorOpen(value => !value); setSidebarOpen(false) }} />
      <div className={`workspace ${inspectorOpen && !memoryOpen ? 'with-inspector' : ''}`}>
        <div className={`chat-column ${empty && !memoryOpen ? 'is-empty' : ''}`}>
          {empty && !memoryOpen && <AgentCompanion activity={chat.activity} sessionId={chat.activeId} compact={false} />}
          <MemoryWorkspace api={api} visible={memoryOpen} activityKey={chat.runningIds.size}
            onOpenSession={id => { showChat(); void chat.loadConversation(id) }} />
          <main className="chat-content" id="conversation" hidden={memoryOpen}>
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
            {empty && <div className="starter-actions" aria-label="任务灵感">
              <button onClick={() => choosePrompt('帮我分析这个项目的代码结构：')}><Code2 size={17} /><span>分析代码</span><ArrowUpRight size={15} /></button>
              <button onClick={() => choosePrompt('帮我拆解这个任务，制定可执行的计划：')}><ListTodo size={17} /><span>拆解任务</span><ArrowUpRight size={15} /></button>
              <button onClick={() => choosePrompt('帮我回顾已有记忆，整理相关的经验：')}><BookOpen size={17} /><span>回顾记忆</span><ArrowUpRight size={15} /></button>
            </div>}
          </div>
          </main>
        </div>
        {inspectorOpen && !memoryOpen && <InspectorPanel session={chat.session} status={chat.status} queued={chat.queued}
          selectedCard={chat.selectedCard} childSession={chat.childSession} childMessages={chat.childMessages}
          childStreaming={chat.childStreaming} onMainAgent={() => chat.selectSubAgent(null)} onClose={() => setInspectorOpen(false)} />}
      </div>
      </div>
    </div>
  )
}
