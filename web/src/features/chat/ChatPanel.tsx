import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../../lib/types'
import { ExecutionReport } from './ExecutionReport'

type ChatPanelProps = {
  messages: Message[]
}

export function ChatPanel({ messages }: ChatPanelProps) {
  const historyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (historyRef.current) historyRef.current.scrollTop = historyRef.current.scrollHeight
  }, [messages])

  return (
    <section className="chat-panel">
      <div className="chat-history" ref={historyRef}>
        {messages.length === 0 && <div className="chat-empty">输入任务，开始与 Agent 对话</div>}
        {messages.map((message) => (
          <article key={message.id} className={`chat-message ${message.role}`}>
            {message.kind === 'execution_report' ? (
              <ExecutionReport content={message.content} />
            ) : message.role === 'tool' ? (
              <details className="tool-result">
                <summary>工具结果 · {message.toolName ?? '工具'}</summary>
                <pre>{message.content}</pre>
              </details>
            ) : (
              <>
                <div className="message-role">{message.role === 'user' ? '你' : 'Agent'}</div>
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
