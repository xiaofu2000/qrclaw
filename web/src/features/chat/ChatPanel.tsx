import type { Agent, Message, RunSnapshot } from '../../models/workbench'
import { MarkdownContent } from '../../components/MarkdownContent'
import { TimelinePanel } from '../timeline/TimelinePanel'

type ChatPanelProps = {
  messages: Message[]
  snapshot: RunSnapshot | null
  selectedAgent: Agent | null
  onSelectAgent: (id: string | null) => void
}

/** 渲染一条会话消息，Agent 回复使用 Markdown。 */
function MessageArticle({ message }: { message: Message }) {
  return (
    <article className={`chat-message ${message.role}`}>
      <div className="message-role">{message.role === 'user' ? '你' : 'Agent'}</div>
      <div className="message-bubble">
        {message.role === 'assistant' ? (
          <MarkdownContent content={message.content} />
        ) : (
          message.content
        )}
      </div>
    </article>
  )
}

/**
 * 中栏会话与执行流。
 * 当前运行插入到最近一条用户消息和 Agent 最终回答之间，保持真实阅读顺序。
 */
export function ChatPanel({ messages, snapshot, selectedAgent, onSelectAgent }: ChatPanelProps) {
  if (!messages.length && !snapshot) {
    return (
      <section className="chat-panel empty-state">
        <div className="empty-icon">QR</div>
        <h2>开始一个本地任务</h2>
        <p>输入目标后，可在这里查看当前任务、Agent、工具调用和最终结果。</p>
      </section>
    )
  }

  const latestUserIndex = snapshot
    ? messages.findLastIndex((message) => message.role === 'user')
    : -1
  const messagesBeforeRun = latestUserIndex >= 0
    ? messages.slice(0, latestUserIndex + 1)
    : snapshot
      ? []
      : messages
  const messagesAfterRun = latestUserIndex >= 0
    ? messages.slice(latestUserIndex + 1)
    : snapshot
      ? messages
      : []
  const restoredTaskMessage: Message | null = snapshot && latestUserIndex < 0
    ? {
        id: `restored-task-${snapshot.run.id}`,
        role: 'user',
        content: snapshot.run.goal,
      }
    : null

  return (
    <section className="chat-panel">
      <div className="chat-history">
        {messagesBeforeRun.map((message) => (
          <MessageArticle key={message.id} message={message} />
        ))}
        {restoredTaskMessage && <MessageArticle message={restoredTaskMessage} />}
        {snapshot && <TimelinePanel snapshot={snapshot} selectedAgent={selectedAgent} onSelectAgent={onSelectAgent} />}
        {messagesAfterRun.map((message) => (
          <MessageArticle key={message.id} message={message} />
        ))}
      </div>
    </section>
  )
}
