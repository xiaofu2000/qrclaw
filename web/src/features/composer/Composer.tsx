import { useLayoutEffect, useRef } from 'react'
import { ArrowUp, LoaderCircle } from 'lucide-react'

type ComposerProps = {
  value: string
  onChange: (value: string) => void
  onSend: (content: string) => void | Promise<void>
  disabled: boolean
  loading: boolean
  running: boolean
  planEnabled: boolean
  onTogglePlan: () => void
  contextUsage: { label: string; percent: number; description: string } | null
  compressionNotice: { state?: string; message: string } | null
}

/** 输入从单行开始增高，发送同排，执行模式和上下文位于下方工具行。 */
export function Composer({ value, onChange, onSend, disabled, loading, running, planEnabled, onTogglePlan,
  contextUsage, compressionNotice }: ComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const input = inputRef.current
    if (input) {
      input.style.height = 'auto'
      input.style.height = `${Math.min(input.scrollHeight, 180)}px`
    }
  }, [value])

  /** 保留运行中禁发及空内容保护，中文输入法的确认键不触发提交。 */
  function submit() {
    if (!disabled && value.trim()) void onSend(value.trim())
  }

  return (
    <form className="composer" onSubmit={event => { event.preventDefault(); submit() }}>
      {compressionNotice && <div className={`compression-notice ${compressionNotice.state}`} role="status">
        {compressionNotice.state === 'started' && <LoaderCircle size={14} className="spin" />}
        <span>{compressionNotice.message}</span>
      </div>}
      <div className="composer-box">
        <label className="sr-only" htmlFor="message-input">消息</label>
        <textarea id="message-input" ref={inputRef} value={value} onChange={event => onChange(event.target.value)}
          placeholder={running ? '可以先写下下一条消息…' : '描述任务，或接着聊…'} rows={1} disabled={loading}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
              event.preventDefault()
              submit()
            }
          }} />
        <button className="send-button" type="submit" disabled={disabled || !value.trim()} aria-label={running ? '任务处理中' : '发送消息'} title={running ? '等待当前任务完成' : '发送消息'}>
          {running ? <LoaderCircle size={20} className="spin" /> : <ArrowUp size={20} />}
        </button>
      </div>
      <div className="composer-toolbar">
        <button type="button" className="plan-switch" role="switch" aria-label="启用 Plan 模式"
          aria-checked={planEnabled} disabled={disabled} onClick={onTogglePlan}
          title="开启后允许自动拆分计划；关闭后直接执行。下次发送生效。">
          <span className="switch-track" aria-hidden="true"><span /></span>Plan 模式
        </button>
        {contextUsage && <div className="context-usage" title={contextUsage.description} aria-label={`上下文用量估算 ${contextUsage.label} tokens，${contextUsage.percent}%`}>
          <span>上下文 <strong>{contextUsage.percent}%</strong></span>
          <meter min={0} max={100} value={contextUsage.percent} aria-label="上下文占用比例" />
        </div>}
      </div>
      <div className="composer-hint">{running ? '任务处理中，切换会话可以继续查看其他对话。' : 'Enter 发送 · Shift+Enter 换行'}</div>
    </form>
  )
}
