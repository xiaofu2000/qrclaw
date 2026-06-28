import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../../lib/types'
import { ExecutionReport } from './ExecutionReport'

type ChatPanelProps = {
  messages: Message[]
}

export function ChatPanel({ messages }: ChatPanelProps) {
  return (
    <section className="chat-panel">
      <div className="chat-history">
        {messages.map((message) => (
          <article key={message.id} className={`chat-message ${message.role}`}>
            {message.kind === 'execution_report' ? (
              <ExecutionReport content={message.content} />
            ) : (
              <>
                <div className="message-role">{message.role}</div>
                <div className="message-bubble markdown-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                </div>
              </>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}
