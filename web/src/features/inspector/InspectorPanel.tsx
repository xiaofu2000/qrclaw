import type { RunStatus, SessionDetail } from '../../lib/types'

type InspectorPanelProps = {
  session?: SessionDetail | null
  status: RunStatus
}

export function InspectorPanel({ session, status }: InspectorPanelProps) {
  const events = session?.events ?? []

  return (
    <aside className="inspector-panel">
      <section>
        <div className="panel-title">运行状态</div>
        <div className="panel-value">{status === 'busy' ? '任务运行中' : session ? '本地服务已连接' : '等待会话载入'}</div>
      </section>
      <section>
        <div className="panel-title">执行过程</div>
        <div className="inspector-events">
          {events.length > 0 ? (
            events.slice(-6).map((event) => (
              <div key={`${event.kind}-${event.created_at}-${event.title}`} className="inspector-event">
                <div className="inspector-event-title">{event.title}</div>
                <div className="inspector-event-content">{event.content}</div>
              </div>
            ))
          ) : (
            <div className="panel-value muted">{status === 'busy' ? '正在执行任务' : '暂无过程记录'}</div>
          )}
        </div>
      </section>
    </aside>
  )
}
