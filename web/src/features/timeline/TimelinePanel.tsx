import type { Agent, RunSnapshot } from '../../models/workbench'

type TimelinePanelProps = { snapshot: RunSnapshot; selectedAgent: Agent | null; onSelectAgent: (id: string | null) => void }
const statusLabels: Record<string, string> = { pending: '等待', running: '运行中', waiting_approval: '待授权', completed: '完成', failed: '失败', denied: '已拒绝', cancelled: '已取消', queued: '排队中', routing: '路由中', cancelling: '取消中' }

/** 格式化工具参数，限制页面默认展开长度。 */
function formatArguments(value: Record<string, unknown>): string { const result = JSON.stringify(value, null, 2); return result.length > 500 ? `${result.slice(0, 500)}\n…` : result }

/** 计划、Agent、工具和变更组成的结构化运行时间线。 */
export function TimelinePanel({ snapshot, selectedAgent, onSelectAgent }: TimelinePanelProps) {
  const { run, plan, agents, toolCalls, fileChanges } = snapshot
  return <div className="run-timeline">
    <div className="timeline-card route-card"><div className="card-heading"><strong>路由：{run.route === 'plan' ? '计划执行' : run.route === 'direct' ? '直接执行' : '判断中'}</strong><span>{statusLabels[run.status] ?? run.status}</span></div><p>{run.goal}</p></div>
    {plan && <div className="timeline-card plan-card"><div className="card-heading"><strong>计划已创建</strong><span>{plan.steps.length} 个步骤 · 修订 {plan.revision}</span></div><div className="step-list">{plan.steps.map((step, index) => {
      const stepAgents = agents.filter((agent) => agent.stepId === step.id)
      return <section key={step.id} className={`step-card status-${step.status}`}><div className="card-heading"><strong>步骤 {index + 1}：{step.description}</strong><span>{statusLabels[step.status]}</span></div>{step.dependsOn.length > 0 && <div className="dependency-text">依赖：{step.dependsOn.join('、')}</div>}{stepAgents.length > 0 && <div className="agent-grid">{stepAgents.map((agent) => <button key={agent.id} type="button" className={`agent-card status-${agent.status} ${selectedAgent?.id === agent.id ? 'selected' : ''}`} onClick={() => onSelectAgent(agent.id)}><span><strong>{agent.name}</strong><em>{statusLabels[agent.status]}</em></span><small>{agent.currentAction || agent.task || '等待开始'}</small></button>)}</div>}{step.output && <details><summary>查看步骤结果</summary><pre>{step.output}</pre></details>}</section>
    })}</div></div>}
    {!plan && agents.length > 0 && <div className="timeline-card"><div className="card-heading"><strong>Agent 执行</strong></div><div className="agent-grid">{agents.map((agent) => <button key={agent.id} type="button" className={`agent-card status-${agent.status}`} onClick={() => onSelectAgent(agent.id)}><span><strong>{agent.name}</strong><em>{statusLabels[agent.status]}</em></span><small>{agent.currentAction || agent.task}</small></button>)}</div></div>}
    {toolCalls.map((tool) => <details key={tool.id} className={`timeline-card tool-card status-${tool.status}`}><summary><strong>工具：{tool.name}</strong><span>{statusLabels[tool.status] ?? tool.status}</span></summary><pre>{formatArguments(tool.arguments)}</pre>{(tool.result || tool.error) && <pre className={tool.error ? 'error-text' : ''}>{tool.error || tool.result}</pre>}</details>)}
    {fileChanges.map((change, index) => <details key={`${String(change.path ?? 'change')}-${index}`} className="timeline-card file-card"><summary><strong>文件变更：{String(change.path ?? change.file ?? '未知文件')}</strong></summary><pre>{JSON.stringify(change, null, 2)}</pre></details>)}
  </div>
}
