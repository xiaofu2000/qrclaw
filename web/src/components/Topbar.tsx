import type { ConnectionState, Run } from '../models/workbench'

type TopbarProps = {
  workspaceTitle: string
  connection: ConnectionState
  run: Run | null
  canCancel: boolean
  onCancel: () => void
  onOpenSettings: () => void
}

const connectionLabels: Record<ConnectionState, string> = {
  connecting: '正在连接',
  connected: '本地服务已连接',
  disconnected: '连接已断开，正在恢复',
  error: '本地服务异常',
}

const runLabels: Record<string, string> = {
  queued: '任务排队中', routing: '正在判断路由', running: '任务运行中',
  waiting_approval: '等待工具授权', cancelling: '正在取消', completed: '任务已完成',
  failed: '任务失败', cancelled: '任务已取消',
}

/** 工作台固定顶栏。 */
export function Topbar({ workspaceTitle, connection, run, canCancel, onCancel, onOpenSettings }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="brand-row">
        <span className="brand-mark">QR</span>
        <strong>QRClaw</strong>
      </div>
      <div className="workspace-title">{workspaceTitle || '本地工作台'}</div>
      <div className="topbar-actions">
        {run && <span className={`run-label status-${run.status}`}>{runLabels[run.status]}</span>}
        {canCancel && <button className="text-button danger" type="button" onClick={onCancel}>停止任务</button>}
        <span className={`connection-status ${connection}`}><i aria-hidden="true" />{connectionLabels[connection]}</span>
        <button className="text-button" type="button" onClick={onOpenSettings}>设置</button>
      </div>
    </header>
  )
}
