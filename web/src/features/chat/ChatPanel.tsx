import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ArrowDown, BookOpen, Check, Copy, GitBranch, Pencil, Trash2, Wrench } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatTimelineItem, Message, SubAgentCard } from '../../lib/types'
import { ExecutionReport } from './ExecutionReport'

type ChatPanelProps = {
  sessionId: string
  title?: string
  messageCount?: number
  compressions?: number
  assistantAvatar?: ReactNode
  timeline: ChatTimelineItem[]
  onSelectSubAgent: (childSessionId: string) => void
  onOpenMemory?: (page?: string) => void
  streaming: string
  running: boolean
  queued: boolean
  loading: boolean
  mutating: boolean
  visible: boolean
  unreadReplyId: string | null
  onReplyRead: (sessionId: string, replyId: string) => void
  latestUserMessageId?: string
  onEditLatestMessage: (messageId: string, content: string) => void
  onDeleteLatestMessage: (messageId: string) => void
}

type Segment = { kind: 'tools'; id: string; tools: Message[] } | { kind: 'reply'; message: Message }
  | { kind: 'report'; message: Message } | { kind: 'card'; card: SubAgentCard }
type Turn = { id: string; user?: Message; segments: Segment[] }
type Filter = 'all' | 'talk' | 'work'

const STATE_LABEL: Record<SubAgentCard['state'], string> = { running: '运行中', completed: '已完成', failed: '失败', interrupted: '已中断' }

/** 每条用户消息开启一轮；到下一条用户消息之前的回复、工具、子任务都属于 QRClaw 这一轮，连续的工具调用合并成一段。 */
function groupTurns(timeline: ChatTimelineItem[]): Turn[] {
  const turns: Turn[] = []
  for (const item of timeline) {
    if (!('childSessionId' in item) && item.role === 'user') { turns.push({ id: item.id, user: item, segments: [] }); continue }
    if (!turns.length) turns.push({ id: `start-${'id' in item ? item.id : ''}`, segments: [] })
    const segments = turns[turns.length - 1].segments
    if ('childSessionId' in item) { segments.push({ kind: 'card', card: item }); continue }
    if (item.role === 'tool') {
      const last = segments[segments.length - 1]
      if (last?.kind === 'tools') last.tools.push(item)
      else segments.push({ kind: 'tools', id: item.id, tools: [item] })
      continue
    }
    segments.push(item.kind === 'execution_report' ? { kind: 'report', message: item } : { kind: 'reply', message: item })
  }
  return turns
}

/** 工具名按出现次数汇总，供折叠行一眼看出这一段做了什么。 */
function toolCounts(tools: Message[]) {
  const counts = new Map<string, number>()
  for (const tool of tools) counts.set(tool.toolName ?? '工具', (counts.get(tool.toolName ?? '工具') ?? 0) + 1)
  return [...counts]
}

/** 聊天视图：你的消息在右侧气泡，QRClaw 在左侧带头像的卡片；并提供最后一条用户消息的编辑入口。 */
export function ChatPanel({ sessionId, title, messageCount = 0, compressions = 0, assistantAvatar, timeline, onSelectSubAgent, onOpenMemory, streaming, running, queued, loading, mutating,
  visible, unreadReplyId, onReplyRead, latestUserMessageId, onEditLatestMessage, onDeleteLatestMessage }: ChatPanelProps) {
  const historyRef = useRef<HTMLDivElement>(null)
  const readMarkerRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  const previousSessionRef = useRef(sessionId)
  const [showJump, setShowJump] = useState(false)
  const [copiedId, setCopiedId] = useState('')
  const [copyError, setCopyError] = useState('')
  const [editingId, setEditingId] = useState('')
  const [editContent, setEditContent] = useState('')
  const [filter, setFilter] = useState<Filter>('all')

  useLayoutEffect(() => {
    if (!visible) return
    if (sessionId !== previousSessionRef.current) {
      followRef.current = true
      previousSessionRef.current = sessionId
      setEditingId('')
    }
    const history = historyRef.current
    if (history && followRef.current) {
      history.scrollTop = history.scrollHeight
      setShowJump(false)
    } else if (history) setShowJump(true)
  }, [sessionId, timeline, streaming, running, visible])

  useEffect(() => {
    if (!visible || !unreadReplyId || running || loading || mutating) return
    const history = historyRef.current, marker = readMarkerRef.current
    if (!history || !marker) return
    let inView = false, cancelled = false
    /** 仅前台聊天实际看到回复末尾时确认已读，后台完成不会自动清除提示。 */
    function acknowledge() {
      if (cancelled || !inView || document.visibilityState !== 'visible' || !document.hasFocus()) return
      const bounds = marker!.getBoundingClientRect(), viewport = history!.getBoundingClientRect()
      // 滚动高度会取整，允许末尾标记有不足一像素的偏差。
      if (history!.clientHeight > 0 && bounds.top >= viewport.top && bounds.bottom <= viewport.bottom + 1) onReplyRead(sessionId, unreadReplyId!)
    }
    const observer = new IntersectionObserver(entries => {
      inView = entries.some(entry => entry.isIntersecting)
      acknowledge()
    }, { root: history, threshold: 0 })
    observer.observe(marker)
    document.addEventListener('visibilitychange', acknowledge)
    window.addEventListener('focus', acknowledge)
    return () => {
      cancelled = true
      observer.disconnect()
      document.removeEventListener('visibilitychange', acknowledge)
      window.removeEventListener('focus', acknowledge)
    }
  }, [sessionId, visible, unreadReplyId, running, loading, mutating, onReplyRead])

  async function copy(message: Message) {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopiedId(message.id)
      setCopyError('')
    } catch { setCopyError('复制失败，请选中文字后复制。') }
  }

  const turns = groupTurns(timeline)
  const showTalk = filter !== 'work', showWork = filter !== 'talk'

  /** QRClaw 一轮里的一段：回复气泡、合并后的工具调用、子任务卡片或执行汇报。 */
  function renderSegment(segment: Segment, lastReplyId?: string) {
    if (segment.kind === 'reply') return showTalk && <div key={segment.message.id}>
      <div className="message-bubble markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{segment.message.content}</ReactMarkdown></div>
      {segment.message.id === lastReplyId && <div className="message-actions"><button type="button" className="copy-button" aria-label="复制回复" onClick={() => void copy(segment.message)}>
        {copiedId === segment.message.id ? <Check size={14} /> : <Copy size={14} />}{copiedId === segment.message.id ? '已复制' : '复制'}
      </button></div>}
    </div>
    if (!showWork) return null
    if (segment.kind === 'report') return <ExecutionReport key={segment.message.id} content={segment.message.content} />
    if (segment.kind === 'card') {
      const { card } = segment
      return <button key={card.id} type="button" className="subagent-card" onClick={() => onSelectSubAgent(card.childSessionId)}
        aria-label={`查看子 Agent ${card.agentId} 的执行过程：${card.task}`}>
        <GitBranch size={17} aria-hidden="true" />
        <span className="subagent-card-body"><strong>启动 {card.agentId} 子 Agent</strong><span>{card.task}</span></span>
        <span className={`subagent-state ${card.state}`}>{STATE_LABEL[card.state]}</span>
      </button>
    }
    const memoryWrites = segment.tools.filter(tool => tool.toolName === 'write_wiki_page')
    const pages = [...new Set(memoryWrites.flatMap(tool => tool.toolPages ?? []))]
    const failures = segment.tools.filter(tool => tool.toolState === 'failed').length
    return <div key={segment.id}>
      <details className="turn-run">
        <summary><Wrench size={16} aria-hidden="true" /><span>调用了 {segment.tools.length} 次工具{failures > 0 ? `，${failures} 次失败` : ''}</span>
          <span className="tool-chips">{toolCounts(segment.tools).map(([name, count]) => <span key={name} className="chip">{name}{count > 1 ? ` ×${count}` : ''}</span>)}</span></summary>
        <div className="turn-run-list">{segment.tools.map(tool => <details key={tool.id} className={`tool-result ${tool.toolState === 'failed' ? 'failed' : ''}`}>
          <summary><span className="tool-name">{tool.toolName ?? '工具'}</span>{tool.toolSummary && <span className="tool-summary">{tool.toolSummary}</span>}
            {tool.toolState === 'failed' && <span className="tool-failed">失败</span>}</summary><pre>{tool.content}</pre></details>)}</div>
      </details>
      {memoryWrites.length > 0 && <div className="turn-note"><BookOpen size={16} aria-hidden="true" />{pages.length ? '写入记忆' : `写入记忆 ${memoryWrites.length} 次`}
        {onOpenMemory && (pages.length ? pages.map(page => <button key={page} type="button" onClick={() => onOpenMemory(page)}>{page}</button>)
          : <button type="button" onClick={() => onOpenMemory()}>查看记忆库</button>)}</div>}
    </div>
  }

  return (
    <section className="chat-panel" aria-label="对话内容" aria-busy={loading}>
      <div className="chat-history" ref={historyRef} onScroll={() => {
        if (!visible) return
        const history = historyRef.current
        if (history) {
          followRef.current = history.scrollHeight - history.scrollTop - history.clientHeight < 80
          setShowJump(!followRef.current)
        }
      }}>
        {loading && !timeline.length && <div className="chat-skeleton" role="status"><span className="sr-only">正在载入会话…</span><i /><i /><i /></div>}
        <div className="message-list">
          {title && <div className="chat-heading"><h1>{title}</h1>
            <p>{messageCount} 条消息{compressions > 0 ? ` · 上下文已整理 ${compressions} 次` : ''}</p></div>}
          {title && <div className="chat-tabs" role="tablist" aria-label="筛选对话内容">
            {([['all', '全部'], ['talk', '只看对话'], ['work', '只看过程']] as const).map(([value, label]) =>
              <button key={value} type="button" role="tab" aria-selected={filter === value} onClick={() => setFilter(value)}>{label}</button>)}
          </div>}
          {compressions > 0 && showWork && <div className="sys-line"><span>较早的对话已整理为摘要，共整理 <strong>{compressions}</strong> 次</span></div>}
          {turns.map((turn, index) => {
            const last = index === turns.length - 1
            const user = turn.user
            // 一轮里 QRClaw 可能先说几句再调用工具，只在最后一段回复下放复制按钮，避免每段都重复。
            const lastReply = turn.segments.findLast(segment => segment.kind === 'reply')
            const agentBody = turn.segments.map(segment => renderSegment(segment, lastReply?.kind === 'reply' ? lastReply.message.id : undefined))
            const live = last && (streaming || running)
            return <div key={turn.id}>
              {user && showTalk && <article className="chat-message user">
                <div className="message-body">
                  {editingId === user.uuid && user.uuid ? <div className="message-edit">
                    <textarea aria-label="编辑最后一条用户消息" value={editContent} onChange={event => setEditContent(event.target.value)} rows={Math.max(3, Math.min(10, editContent.split('\n').length + 1))} />
                    <p>保存后将重新执行 Agent；此前执行的外部操作不会撤销。</p>
                    <div className="message-actions">
                      <button type="button" disabled={!editContent.trim() || running || loading || mutating} onClick={() => {
                        onEditLatestMessage(user.uuid!, editContent)
                        setEditingId('')
                      }}>保存并重新执行</button>
                      <button type="button" onClick={() => setEditingId('')}>取消</button>
                    </div>
                  </div> : <div className="message-bubble">{user.content}</div>}
                  {user.uuid && user.uuid === latestUserMessageId && !running && !loading && !mutating && editingId !== user.uuid &&
                    <div className="message-actions">
                      <button type="button" aria-label="编辑最后一条用户消息" onClick={() => { setEditingId(user.uuid!); setEditContent(user.content) }}><Pencil size={13} />编辑</button>
                      <button type="button" aria-label="删除最后一条用户消息" onClick={() => {
                        if (window.confirm('将删除这条消息及之后的 Agent 回复和执行记录。已经执行的外部操作不会撤销。确定删除吗？')) onDeleteLatestMessage(user.uuid!)
                      }}><Trash2 size={13} />删除</button>
                    </div>}
                </div>
                <span className="user-avatar" aria-hidden="true">你</span>
              </article>}
              {(turn.segments.length > 0 || live) && <article className="chat-message assistant" aria-label="QRClaw 的回复">
                {assistantAvatar}
                <div className="message-body">
                  <div className="message-meta"><strong>QRClaw</strong>{live && <span>{queued ? '排队中' : '工作中'}</span>}</div>
                  {agentBody}
                  {last && streaming && showTalk && <div className="message-bubble markdown-body" aria-label="正在生成的回复"><ReactMarkdown remarkPlugins={[remarkGfm]}>{streaming}</ReactMarkdown></div>}
                  {last && running && <div className="pending-response" role="status"><span className="thinking-dots" aria-hidden="true"><i /><i /><i /></span>{queued ? '任务已排队，等待执行…' : '正在处理任务…'}</div>}
                </div>
              </article>}
            </div>
          })}
          {copyError && <div className="copy-error" role="alert">{copyError}</div>}
        </div>
        <div ref={readMarkerRef} aria-hidden="true" style={{ height: 1 }} />
      </div>
      {showJump && <button className="jump-latest" onClick={() => {
        followRef.current = true
        if (historyRef.current) historyRef.current.scrollTop = historyRef.current.scrollHeight
        setShowJump(false)
      }}><ArrowDown size={15} />回到最新</button>}
    </section>
  )
}
