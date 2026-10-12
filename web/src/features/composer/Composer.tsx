import { useLayoutEffect, useRef } from 'react'
import { ArrowUp, LoaderCircle } from 'lucide-react'

type ComposerProps = {
  value: string
  onChange: (value: string) => void
  onSend: (content: string) => void | Promise<void>
  disabled: boolean
  loading: boolean
  running: boolean
}

export function Composer({ value, onChange, onSend, disabled, loading, running }: ComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const input = inputRef.current
    if (input) {
      input.style.height = 'auto'
      input.style.height = `${Math.min(input.scrollHeight, 180)}px`
    }
  }, [value])

  function submit() {
    if (!disabled && value.trim()) void onSend(value.trim())
  }

  return (
    <form className="composer" onSubmit={event => { event.preventDefault(); submit() }}>
      <label className="sr-only" htmlFor="message-input">消息</label>
      <div className="composer-input">
        <textarea id="message-input" ref={inputRef} value={value} onChange={event => onChange(event.target.value)}
          placeholder={running ? '可以先写下下一条消息…' : '发送消息或描述任务…'} rows={1} disabled={loading}
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
      <div className="composer-hint">{running ? '任务处理中，切换会话可以继续查看其他对话。' : 'Enter 发送 · Shift+Enter 换行'}</div>
    </form>
  )
}
