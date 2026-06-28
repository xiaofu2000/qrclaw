import type { RunStatus } from '../lib/types'

type TopbarProps = {
  status: RunStatus
  statusText: string
}

export function Topbar({ status, statusText }: TopbarProps) {
  return (
    <header className="topbar">
      <div>
        <div className="brand">QRClaw</div>
        <div className="subtitle">Local agent workspace</div>
      </div>
      <div className={`status-pill ${status}`}>{statusText}</div>
    </header>
  )
}

