import type { Message, RunStatus, SessionDetail, SubAgentCard } from '../../lib/types'
import { ArrowLeft, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type InspectorPanelProps = {
  session?: SessionDetail | null
  status: RunStatus
  queued: boolean
  selectedCard: SubAgentCard | null
  childSession: SessionDetail | null
  childMessages: Message[]
  childStreaming: string
  onMainAgent: () => void
  onClose: () => void
}

/** 主 Agent 过程与所选子 Agent 完整会话共用右侧面板。 */
export function InspectorPanel({ session, status, queued, selectedCard, childSession, childMessages,
  childStreaming, onMainAgent, onClose }: InspectorPanelProps) {
  const events = (session?.events ?? []).filter(event =>
    event.kind === 'tool_status' || (event.kind === 'status' && ['执行中', '步骤完成', '步骤失败'].includes(event.title)))
  const childEvents = (childSession?.events ?? []).filter(event => event?.kind === 'tool_status' || event?.title === '执行失败')

  return (
    <aside className="inspector-panel" id="run-inspector" aria-label="执行过程">
      <div className="inspector-heading"><strong>执行过程</strong><button className="icon-button" aria-label="关闭执行过程" onClick={onClose}><X size={18} /></button></div>
      {selectedCard ? <>
        <button className="inspector-back" onClick={onMainAgent}><ArrowLeft size={15} />主 Agent</button>
        <section>
          <div className="panel-title">{selectedCard.agentId} 子 Agent</div>
          <div className="panel-value">{selectedCard.task}</div>
          <div className={`subagent-state ${selectedCard.state}`}>{selectedCard.state === 'running' ? '运行中' : selectedCard.state === 'completed' ? '已完成' : selectedCard.state === 'failed' ? '失败' : '已中断'}</div>
        </section>
        <section>
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
        <section>
          <div className="panel-title">工具状态</div>
          <div className="inspector-events">
            {childEvents.length ? childEvents.map((event, index) => <div key={`${index}-${event.created_at}`} className="inspector-event">
              <div className="inspector-event-title">{event.title}</div>
              <div className="inspector-event-content">{event.content}</div>
            </div>) : <div className="panel-value muted">暂无工具记录</div>}
          </div>
        </section>
      </> : <>
      <section>
        <div className="panel-title">运行状态</div>
        <div className="panel-value">{queued ? '等待执行' : status === 'busy' ? '正在处理任务' : status === 'error' ? '请检查会话中的错误信息' : session ? '等待新任务' : '等待会话载入'}</div>
      </section>
      <section>
        <div className="panel-title">最近记录</div>
        <div className="inspector-events">
          {events.length > 0 ? (
            events.slice(-6).map((event) => (
              <div key={`${event.kind}-${event.created_at}-${event.title}`} className="inspector-event">
                <div className="inspector-event-title">{event.title}</div>
                <div className="inspector-event-content">{event.content}</div>
              </div>
            ))
          ) : (
            <div className="panel-value muted">{status === 'busy' ? '等待过程记录…' : '暂无过程记录'}</div>
          )}
        </div>
      </section>
      </>}
    </aside>
  )
}
