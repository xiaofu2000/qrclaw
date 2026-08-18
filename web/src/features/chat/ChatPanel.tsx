import type { Agent, Message, RunSnapshot } from '../../models/workbench'
import { TimelinePanel } from '../timeline/TimelinePanel'

type ChatPanelProps = {
  messages: Message[]
  snapshot: RunSnapshot | null
  selectedAgent: Agent | null
  onSelectAgent: (id: string | null) => void
}

/** 中栏对话与任务执行时间线。 */
export function ChatPanel({ messages, snapshot, selectedAgent, onSelectAgent }: ChatPanelProps) {
  if (!messages.length && !snapshot) return <section className="chat-panel empty-state"><div className="empty-icon">QR</div><h2>开始一个本地任务</h2><p>输入目标后，可在这里查看计划、Agent、工具调用和文件变更。</p></section>
  return (
    <section className="chat-panel">
      <div className="chat-history">
        {messages.map((message) => (
          <article key={message.id} className={`chat-message ${message.role}`}>
            <div className="message-role">{message.role === 'user' ? '你' : 'Agent'}</div>
            <div className="message-bubble">{message.content}</div>
          </article>
        ))}
        {snapshot && <TimelinePanel snapshot={snapshot} selectedAgent={selectedAgent} onSelectAgent={onSelectAgent} />}
      </div>
    </section>
  )
}
