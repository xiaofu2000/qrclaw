import type { Message } from '../../lib/types'

type ChatPanelProps = {
  messages: Message[]
}

export function ChatPanel({ messages }: ChatPanelProps) {
  return (
    <section className="chat-panel">
      <div className="chat-history">
        {messages.map((message) => (
          <article key={message.id} className={`chat-message ${message.role}`}>
            <div className="message-role">{message.role}</div>
            <div className="message-bubble">{message.content}</div>
          </article>
        ))}
      </div>
    </section>
  )
}

