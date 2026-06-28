import { useState } from 'react'

type ComposerProps = {
  onSend: (content: string) => void | Promise<void>
  disabled?: boolean
}

export function Composer({ onSend, disabled }: ComposerProps) {
  const [value, setValue] = useState('')

  async function submit() {
    const trimmed = value.trim()
    if (!trimmed) return
    setValue('')
    await onSend(trimmed)
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
        placeholder="输入任务，Enter 发送，Shift+Enter 换行"
        rows={3}
        disabled={disabled}
      />
      <button type="submit" disabled={disabled}>
        发送
      </button>
    </form>
  )
}

