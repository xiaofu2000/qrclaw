import type { Agent, Approval } from '../../models/workbench'

type ToolApprovalDialogProps = { approval: Approval; agent: Agent | null; pending: boolean; onResolve: (decision: 'allow_once' | 'deny') => void }

/** 阻塞式工具授权对话框，只允许一次或拒绝。 */
export function ToolApprovalDialog({ approval, agent, pending, onResolve }: ToolApprovalDialogProps) {
  const details = approval.details
  const sandbox = details.sandbox && typeof details.sandbox === 'object' ? details.sandbox as Record<string, unknown> : {}
  return <div className="dialog-backdrop" role="presentation"><section className="approval-dialog" role="dialog" aria-modal="true" aria-labelledby="approval-title">
    <header><span className="warning-icon">!</span><div><h2 id="approval-title">{agent?.name ?? 'Agent'} 请求执行命令</h2><p>任务已暂停，等待你的决定</p></div></header>
    <div className="approval-content">
      <dl><dt>工具</dt><dd>{String(details.tool_name ?? '未知工具')}</dd><dt>执行目的</dt><dd>{String(details.purpose ?? '未提供')}</dd><dt>工作目录</dt><dd>{String(details.cwd ?? '未提供')}</dd><dt>执行环境</dt><dd>{sandbox.enabled ? `沙箱 · 网络 ${String(sandbox.network ?? '未知')}` : '本地主机'}</dd></dl>
      <pre>{JSON.stringify(details.arguments ?? {}, null, 2)}</pre>
      <div className={`risk-note risk-${String(details.risk_level ?? 'medium')}`}>风险：{String(details.risk_level ?? 'medium')}。请核对参数和工作目录后再决定。</div>
    </div>
    <footer><button type="button" disabled={pending} onClick={() => onResolve('deny')}>拒绝</button><button className="warning-button" type="button" disabled={pending} onClick={() => onResolve('allow_once')}>允许一次</button></footer>
  </section></div>
}

