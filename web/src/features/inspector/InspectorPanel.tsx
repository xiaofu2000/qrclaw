import type { RunStatus, SessionDetail } from '../../lib/types'
import { X } from 'lucide-react'

type InspectorPanelProps = {
  session?: SessionDetail | null
  status: RunStatus
  queued: boolean
  onClose: () => void
}

export function InspectorPanel({ session, status, queued, onClose }: InspectorPanelProps) {
  const events = session?.events ?? []

  return (
    <aside className="inspector-panel" id="run-inspector" aria-label="执行过程">
      <div className="inspector-heading"><strong>执行过程</strong><button className="icon-button" aria-label="关闭执行过程" onClick={onClose}><X size={18} /></button></div>
      <section>
        <div className="panel-title">运行状态</div>
        <div className="panel-value">{queued ? '等待执行' : status === 'busy' ? '正在处理任务' : status === 'error' ? '请检查会话中的错误信息' : session ? '等待新任务' : '等待会话载入'}</div>
      </section>
      <section>
        <div className="panel-title">最近记录</div>
        <div className="inspector-events">
          {events.length > 0 ? (
            events.slice(-6).map((event) => (
              <div key={`${event.kind}-${event.created_at}-${event.title}`} className="inspector-event">
                <div className="inspector-event-title">{event.title}</div>
                <div className="inspector-event-content">{event.content}</div>
              </div>
            ))
          ) : (
            <div className="panel-value muted">{status === 'busy' ? '等待过程记录…' : '暂无过程记录'}</div>
          )}
        </div>
      </section>
    </aside>
  )
}
