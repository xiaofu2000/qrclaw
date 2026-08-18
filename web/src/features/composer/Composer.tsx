import { useState, type KeyboardEvent } from 'react'

type ComposerProps = {
  onSend: (content: string) => void | Promise<void>
  disabled?: boolean
  running?: boolean
}

/** 固定在中栏底部的任务输入框。 */
export function Composer({ onSend, disabled, running }: ComposerProps) {
  const [value, setValue] = useState('')

  async function submit() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    setValue('')
    await onSend(trimmed)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit() }
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={running ? '任务运行中，可以等待完成或先停止任务' : '输入任务，Enter 发送，Shift+Enter 换行'}
        rows={2}
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !value.trim()}>
        发送
      </button>
    </form>
  )
}
