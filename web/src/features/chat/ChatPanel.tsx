import { useLayoutEffect, useRef, useState } from 'react'
import { ArrowDown, Bot, Check, Copy, GitBranch } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatTimelineItem, Message } from '../../lib/types'
import { ExecutionReport } from './ExecutionReport'

type ChatPanelProps = {
  sessionId: string
  timeline: ChatTimelineItem[]
  onSelectSubAgent: (childSessionId: string) => void
  streaming: string
  running: boolean
  queued: boolean
  loading: boolean
}

export function ChatPanel({ sessionId, timeline, onSelectSubAgent, streaming, running, queued, loading }: ChatPanelProps) {
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
  }, [sessionId, timeline, streaming, running])

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
        {loading && !timeline.length ? <div className="chat-empty">正在载入会话…</div>
          : !timeline.length && <div className="chat-empty"><Bot size={30} strokeWidth={1.5} /><h1>有什么需要我帮你做？</h1><p>分析代码、执行任务，或整理长期记忆。</p></div>}
        <div className="message-list">
          {timeline.map(item => 'childSessionId' in item ? (
            <article key={item.id} className="chat-message subagent">
              <button className="subagent-card" onClick={() => onSelectSubAgent(item.childSessionId)}
                aria-label={`查看子 Agent ${item.agentId} 的执行过程：${item.task}`}>
                <GitBranch size={17} aria-hidden="true" />
                <span className="subagent-card-body"><strong>启动 {item.agentId} 子 Agent</strong><span>{item.task}</span></span>
                <span className={`subagent-state ${item.state}`}>{item.state === 'running' ? '运行中' : item.state === 'completed' ? '已完成' : item.state === 'failed' ? '失败' : '已中断'}</span>
              </button>
            </article>
          ) : (
            <article key={item.id} className={`chat-message ${item.role}`}>
              {item.kind === 'execution_report' ? <ExecutionReport content={item.content} />
                : item.role === 'tool' ? <details className="tool-result"><summary>工具结果 · {item.toolName ?? '工具'}</summary><pre>{item.content}</pre></details>
                : <>
                  <div className="message-role">{item.role === 'user' ? '你' : 'QRClaw'}</div>
                  <div className="message-bubble markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown></div>
                  {item.role === 'assistant' && <button className="copy-button" aria-label="复制回复" onClick={() => void copy(item)}>
                    {copiedId === item.id ? <Check size={14} /> : <Copy size={14} />}{copiedId === item.id ? '已复制' : '复制'}
                  </button>}
                </>}
            </article>
          ))}
          {streaming && <article className="chat-message assistant" aria-label="正在生成的回复">
            <div className="message-role">QRClaw</div>
            <div className="message-bubble markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{streaming}</ReactMarkdown></div>
          </article>}
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
