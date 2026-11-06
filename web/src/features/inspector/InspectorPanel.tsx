import type { AgentActivity, Message, RunStatus, SessionDetail, SubAgentCard } from '../../lib/types'
import { ArrowLeft, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AgentAvatar } from '../../components/AgentAvatar'

type InspectorPanelProps = {
  session?: SessionDetail | null
  status: RunStatus
  queued: boolean
  activity: AgentActivity
  planEnabled: boolean
  contextUsage: { label: string; percent: number; description: string } | null
  cards: SubAgentCard[]
  selectedCard: SubAgentCard | null
  childSession: SessionDetail | null
  childMessages: Message[]
  childStreaming: string
  onSelectSubAgent: (childSessionId: string) => void
  onMainAgent: () => void
  onOpenMemory: () => void
  onClose: () => void
}

const STATE_LABEL: Record<SubAgentCard['state'], string> = { running: '运行中', completed: '已完成', failed: '失败', interrupted: '已中断' }
const fmt = (value: number) => value.toLocaleString('zh-CN')

/** 从会话事件统计工具调用、上下文整理与写入记忆，只读取已有接口字段。 */
function sessionStats(session?: SessionDetail | null) {
  const events = session?.events ?? []
  const tools = new Map<string, number>()
  for (const event of events) {
    if (event?.kind === 'tool_status' && event.meta?.state === 'started') {
      const name = event.meta.tool ?? event.content ?? '工具'
      tools.set(name, (tools.get(name) ?? 0) + 1)
    }
  }
  const compressions = events.filter(event => event?.kind === 'compression' && event.meta?.state === 'completed')
  const toolList = [...tools].sort((a, b) => b[1] - a[1])
  return {
    toolTotal: toolList.reduce((sum, [, count]) => sum + count, 0), toolList,
    memoryWrites: tools.get('write_wiki_page') ?? 0, compressions,
  }
}

/** 会话属性与所选子 Agent 的完整会话共用右侧面板；宽屏常驻，窄屏为抽屉。 */
export function InspectorPanel({ session, status, queued, activity, planEnabled, contextUsage, cards, selectedCard, childSession, childMessages,
  childStreaming, onSelectSubAgent, onMainAgent, onOpenMemory, onClose }: InspectorPanelProps) {
  const events = (session?.events ?? []).filter(event =>
    event?.kind === 'tool_status' || event?.kind === 'compression' || (event?.kind === 'status' && ['执行中', '步骤完成', '步骤失败'].includes(event.title)))
  const childEvents = (childSession?.events ?? []).filter(event => event?.kind === 'tool_status' || event?.kind === 'compression' || event?.title === '执行失败')
  const stats = sessionStats(session)
  const threshold = session?.context_limit && session.compression_threshold ? session.compression_threshold / session.context_limit * 100 : null

  return (
    <aside className="inspector-panel" id="run-inspector" aria-label="会话属性">
      <div className="inspector-heading"><span>{selectedCard ? '子 Agent' : '属性'}</span><button className="icon-button" aria-label="关闭会话属性" onClick={onClose}><X size={18} /></button></div>
      {selectedCard ? <>
        <button className="inspector-back" onClick={onMainAgent}><ArrowLeft size={15} />返回会话属性</button>
        <section className="inspector-section">
          <div className="panel-title">{selectedCard.agentId} 子 Agent</div>
          <div className="panel-value">{selectedCard.task}</div>
          <div className={`subagent-state ${selectedCard.state}`}>{STATE_LABEL[selectedCard.state]}</div>
        </section>
        <section className="inspector-section">
          <div className="panel-title">完整会话</div>
          <div className="inspector-transcript">
            {childMessages.map(message => <div key={message.id} className="inspector-message">
              <strong>{message.role === 'user' ? '任务指令' : message.role === 'tool' ? `工具结果 · ${message.toolName ?? '工具'}` : '子 Agent'}</strong>
              {message.role === 'tool' ? <pre>{message.content}</pre>
                : <div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>}
            </div>)}
            {childStreaming && <div className="inspector-message" aria-label="子 Agent 正在生成的回复">
              <strong>子 Agent · 正在输入</strong>
              <div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{childStreaming}</ReactMarkdown></div>
            </div>}
            {!childSession && <div className="panel-value muted">正在载入子任务会话…</div>}
          </div>
        </section>
        <section className="inspector-section">
          <div className="panel-title">工具状态</div>
          <div className="inspector-events">
            {childEvents.length ? childEvents.map((event, index) => <div key={`${index}-${event.created_at}`} className="inspector-event">
              <div className="inspector-event-title">{event.title}</div>
              <div className="inspector-event-content">{event.content}</div>
            </div>) : <div className="panel-value muted">暂无工具记录</div>}
          </div>
        </section>
      </> : <>
        <div className="prop"><span>状态</span><span className="prop-value">
          <span className={`companion-status ${activity.status}`}><span className="status-dot" aria-hidden="true" />
            {queued ? '等待执行' : status === 'busy' ? activity.label : status === 'error' ? '请检查会话中的错误信息' : session ? activity.label : '等待会话载入'}</span></span></div>
        <div className="prop"><span>执行者</span><span className="prop-value prop-actor"><AgentAvatar size={30} />QRClaw</span></div>
        <div className="prop"><span>Plan 模式</span><span className="prop-value">{planEnabled ? '开启' : '关闭'}<span className="prop-small">下次发送生效</span></span></div>
        <div className="prop-sep" />
        <div className="prop"><span>上下文</span><span className="prop-value" title={contextUsage?.description}>
          {contextUsage ? <>{contextUsage.percent}%
            <span className="meter-bar" aria-hidden="true"><i style={{ width: `${Math.min(100, contextUsage.percent)}%` }} />{threshold !== null && <b style={{ left: `${threshold}%` }} />}</span>
            <span className="prop-small">{contextUsage.label} tokens</span>
            {session?.compression_threshold ? <span className="prop-small">超过 {fmt(session.compression_threshold)} 时自动整理</span> : null}</> : '首次执行后显示'}
        </span></div>
        <div className="prop"><span>已整理</span><span className="prop-value">{stats.compressions.length} 次
          {stats.compressions.slice(-2).map((event, index) => <span key={index} className="prop-small">{fmt(event.meta?.before_tokens ?? 0)} → {fmt(event.meta?.after_tokens ?? 0)}</span>)}</span></div>
        <div className="prop"><span>累计用量</span><span className="prop-value">{fmt(session?.total_tokens ?? 0)} tokens</span></div>
        <div className="prop-sep" />
        <div className="prop"><span>工具调用</span><span className="prop-value">{stats.toolTotal} 次
          {stats.toolList.length > 0 && <span className="tool-breakdown">{stats.toolList.slice(0, 5).map(([name, count]) =>
            [<span key={`${name}-n`}>{name}</span>, <span key={`${name}-c`}>{count}</span>])}</span>}</span></div>
        <div className="prop"><span>子 Agent</span><span className="prop-value">{cards.length ? `${cards.length} 个` : '没有'}
          {cards.length > 0 && <span className="prop-list">{cards.map(card => <button key={card.id} type="button" onClick={() => onSelectSubAgent(card.childSessionId)} title={card.task}>
            <span className={`subagent-state ${card.state}`}>{card.agentId}</span><span>{card.task}</span></button>)}</span>}</span></div>
        <div className="prop"><span>写入记忆</span><span className="prop-value">{stats.memoryWrites} 次
          <span className="prop-list"><button type="button" onClick={onOpenMemory}><span>打开记忆库</span></button></span></span></div>
        <div className="prop-sep" />
        <section className="inspector-section">
          <div className="panel-title">最近记录</div>
          <div className="inspector-events">
            {events.length > 0 ? events.slice(-6).map(event => (
              <div key={`${event.kind}-${event.created_at}-${event.title}`} className="inspector-event">
                <div className="inspector-event-title">{event.title}</div>
                <div className="inspector-event-content">{event.content}</div>
              </div>
            )) : <div className="panel-value muted">{status === 'busy' ? '等待过程记录…' : '暂无过程记录'}</div>}
          </div>
        </section>
      </>}
    </aside>
  )
}
