import type { Agent, RunSnapshot, ToolCall } from '../../models/workbench'

type TimelinePanelProps = {
  snapshot: RunSnapshot
  selectedAgent: Agent | null
  onSelectAgent: (id: string | null) => void
}

const statusLabels: Record<string, string> = {
  pending: '等待',
  running: '运行中',
  waiting_approval: '待授权',
  completed: '完成',
  failed: '失败',
  denied: '已拒绝',
  cancelled: '已取消',
  queued: '排队中',
  routing: '路由中',
  cancelling: '取消中',
}

/** 格式化工具参数，限制页面默认展开长度。 */
function formatArguments(value: Record<string, unknown>): string {
  const result = JSON.stringify(value, null, 2)
  return result.length > 1000 ? `${result.slice(0, 1000)}\n…` : result
}

/** 按工具实际开始时间排序，尚未开始的工具放在末尾。 */
function sortTools(toolCalls: ToolCall[]): ToolCall[] {
  return [...toolCalls].sort((left, right) => {
    if (!left.startedAt) return 1
    if (!right.startedAt) return -1
    return new Date(left.startedAt).getTime() - new Date(right.startedAt).getTime()
  })
}

/**
 * 当前运行的执行流。
 * 动态计划只展示服务端返回的第一条任务，不在主区域铺开整份易变计划。
 */
export function TimelinePanel({ snapshot, selectedAgent, onSelectAgent }: TimelinePanelProps) {
  const { run, plan, agents, toolCalls, fileChanges } = snapshot
  const currentStep = plan?.steps[0] ?? null
  const stepAgents = currentStep
    ? agents.filter((agent) => agent.stepId === currentStep.id)
    : []
  const activeAgents = agents.filter((agent) =>
    ['running', 'waiting_approval'].includes(agent.status),
  )
  const visibleAgents = stepAgents.length > 0
    ? stepAgents
    : activeAgents.length > 0
      ? activeAgents
      : agents.slice(-3)
  const orderedTools = sortTools(toolCalls)

  return (
    <section className="execution-flow" aria-label="当前任务执行过程">
      <header className="execution-flow-header">
        <span className={`route-badge route-${run.route ?? 'pending'}`}>
          {run.route === 'plan' ? '动态任务' : run.route === 'direct' ? '直接执行' : '正在判断'}
        </span>
        <span className={`execution-status status-${run.status}`}>
          {statusLabels[run.status] ?? run.status}
        </span>
      </header>

      <section className={`current-task-card status-${currentStep?.status ?? run.status}`}>
        <div className="current-task-heading">
          <span>当前任务</span>
          {plan && plan.revision > 1 && <small>计划已动态调整 {plan.revision - 1} 次</small>}
        </div>
        <strong>{currentStep?.description ?? run.goal}</strong>
        {currentStep?.dependsOn.length ? (
          <small className="dependency-text">等待依赖：{currentStep.dependsOn.join('、')}</small>
        ) : null}
      </section>

      {visibleAgents.length > 0 && (
        <section className="execution-agents" aria-label="当前 Agent">
          {visibleAgents.map((agent) => (
            <button
              key={agent.id}
              type="button"
              className={`execution-agent status-${agent.status} ${selectedAgent?.id === agent.id ? 'selected' : ''}`}
              onClick={() => onSelectAgent(agent.id)}
            >
              <span className="agent-status-dot" aria-hidden="true" />
              <span className="execution-agent-copy">
                <strong>{agent.name}</strong>
                <small>{agent.currentAction || agent.task || '等待开始'}</small>
              </span>
              <em>{statusLabels[agent.status]}</em>
            </button>
          ))}
        </section>
      )}

      {(orderedTools.length > 0 || fileChanges.length > 0) && (
        <section className="execution-activity" aria-label="执行记录">
          <div className="execution-section-label">执行记录</div>
          {orderedTools.map((tool) => {
            const owner = agents.find((agent) => agent.id === tool.agentId)
            return (
              <details key={tool.id} className={`activity-item tool-activity status-${tool.status}`}>
                <summary>
                  <span className="activity-icon">⌘</span>
                  <span className="activity-copy">
                    <strong>{tool.name}</strong>
                    <small>{owner?.name ?? 'Agent'} · {statusLabels[tool.status] ?? tool.status}</small>
                  </span>
                  <span className="activity-expand">详情</span>
                </summary>
                <div className="activity-detail">
                  <div className="activity-detail-label">参数</div>
                  <pre>{formatArguments(tool.arguments)}</pre>
                  {(tool.result || tool.error) && (
                    <>
                      <div className="activity-detail-label">工具结果</div>
                      <pre className={tool.error ? 'error-text' : ''}>{tool.error || tool.result}</pre>
                    </>
                  )}
                </div>
              </details>
            )
          })}
          {fileChanges.map((change, index) => (
            <details
              key={`${String(change.path ?? 'change')}-${index}`}
              className="activity-item file-activity"
            >
              <summary>
                <span className="activity-icon">±</span>
                <span className="activity-copy">
                  <strong>{String(change.path ?? change.file ?? '文件变更')}</strong>
                  <small>工作区文件已变化</small>
                </span>
                <span className="activity-expand">详情</span>
              </summary>
              <div className="activity-detail"><pre>{JSON.stringify(change, null, 2)}</pre></div>
            </details>
          ))}
        </section>
      )}
    </section>
  )
}
