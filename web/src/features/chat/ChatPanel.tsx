import { useLayoutEffect, useRef, useState } from 'react'
import { ArrowDown, Bot, Check, Copy } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../../lib/types'
import { ExecutionReport } from './ExecutionReport'

type ChatPanelProps = {
  sessionId: string
  messages: Message[]
  running: boolean
  queued: boolean
  loading: boolean
}

export function ChatPanel({ sessionId, messages, running, queued, loading }: ChatPanelProps) {
  const historyRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  const previousSessionRef = useRef(sessionId)
  const [showJump, setShowJump] = useState(false)
  const [copiedId, setCopiedId] = useState('')
  const [copyError, setCopyError] = useState('')

  useLayoutEffect(() => {
    if (sessionId !== previousSessionRef.current) {
      followRef.current = true
      previousSessionRef.current = sessionId
    }
    const history = historyRef.current
    if (history && followRef.current) {
      history.scrollTop = history.scrollHeight
      setShowJump(false)
    } else if (history) setShowJump(true)
  }, [sessionId, messages, running])

  async function copy(message: Message) {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopiedId(message.id)
      setCopyError('')
    } catch { setCopyError('复制失败，请选中文字后复制。') }
  }

  return (
    <section className="chat-panel" aria-label="对话内容" aria-busy={loading}>
      <div className="chat-history" ref={historyRef} onScroll={() => {
        const history = historyRef.current
        if (history) {
          followRef.current = history.scrollHeight - history.scrollTop - history.clientHeight < 80
          setShowJump(!followRef.current)
        }
      }}>
        {loading && !messages.length ? <div className="chat-empty">正在载入会话…</div>
          : !messages.length && <div className="chat-empty"><Bot size={30} strokeWidth={1.5} /><h1>有什么需要我帮你做？</h1><p>分析代码、执行任务，或整理长期记忆。</p></div>}
        <div className="message-list">
          {messages.map(message => (
            <article key={message.id} className={`chat-message ${message.role}`}>
              {message.kind === 'execution_report' ? <ExecutionReport content={message.content} />
                : message.role === 'tool' ? <details className="tool-result"><summary>工具结果 · {message.toolName ?? '工具'}</summary><pre>{message.content}</pre></details>
                : <>
                  <div className="message-role">{message.role === 'user' ? '你' : 'QRClaw'}</div>
                  <div className="message-bubble markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>
                  {message.role === 'assistant' && <button className="copy-button" aria-label="复制回复" onClick={() => void copy(message)}>
                    {copiedId === message.id ? <Check size={14} /> : <Copy size={14} />}{copiedId === message.id ? '已复制' : '复制'}
                  </button>}
                </>}
            </article>
          ))}
          {running && <div className="pending-response" role="status"><span className="thinking-dots" aria-hidden="true"><i /><i /><i /></span>{queued ? '任务已排队，等待执行…' : '正在处理任务…'}</div>}
          {copyError && <div className="copy-error" role="alert">{copyError}</div>}
        </div>
      </div>
      {showJump && <button className="jump-latest" onClick={() => {
        followRef.current = true
        if (historyRef.current) historyRef.current.scrollTop = historyRef.current.scrollHeight
        setShowJump(false)
      }}><ArrowDown size={15} />回到最新</button>}
    </section>
  )
}
