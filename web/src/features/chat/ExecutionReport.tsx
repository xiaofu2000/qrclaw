import { useState } from 'react'

type ExecutionReportProps = {
  content: string
  title?: string
  defaultCollapsed?: boolean
}

export function ExecutionReport({ content, title = '执行汇报', defaultCollapsed = true }: ExecutionReportProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)

  return (
    <div className="execution-report">
      <button
        type="button"
        className="execution-report-toggle"
        onClick={() => setCollapsed((current) => !current)}
      >
        <span className="execution-report-icon" aria-hidden="true">
          {collapsed ? '>' : 'v'}
        </span>
        <span>{title}</span>
      </button>
      {!collapsed ? <div className="execution-report-body">{content}</div> : null}
    </div>
  )
}
