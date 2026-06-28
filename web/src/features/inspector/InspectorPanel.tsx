import type { SessionDetail } from '../../lib/types'

type InspectorPanelProps = {
  session?: SessionDetail | null
}

export function InspectorPanel({ session }: InspectorPanelProps) {
  const events = session?.events ?? []

  return (
    <aside className="inspector-panel">
      <section>
        <div className="panel-title">运行状态</div>
        <div className="panel-value">{session ? '本地服务已连接' : '等待会话载入'}</div>
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
            <div className="panel-value muted">等待任务执行</div>
          )}
        </div>
      </section>
      <section>
        <div className="panel-title">文件变更</div>
        <div className="panel-value muted">暂无</div>
      </section>
    </aside>
  )
}
