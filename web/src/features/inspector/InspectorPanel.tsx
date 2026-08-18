import type { Agent, RunSnapshot } from '../../models/workbench'
import { MarkdownContent } from '../../components/MarkdownContent'

type InspectorPanelProps = { snapshot: RunSnapshot | null; selectedAgent: Agent | null; onCloseAgent: () => void }
const statusLabels: Record<string, string> = { queued: '排队中', routing: '路由中', running: '运行中', waiting_approval: '等待授权', cancelling: '正在取消', completed: '已完成', failed: '失败', cancelled: '已取消', pending: '等待' }

/** 右侧任务或 Agent 结构化检查器。 */
export function InspectorPanel({ snapshot, selectedAgent, onCloseAgent }: InspectorPanelProps) {
  if (!snapshot) return <aside className="inspector-panel"><div className="inspector-header"><strong>任务检查器</strong></div><div className="inspector-empty">发起任务后，这里会显示计划、Agent、工具和变更统计。</div></aside>

  if (selectedAgent) {
    const tools = snapshot.toolCalls.filter((item) => item.agentId === selectedAgent.id)
    const parent = snapshot.agents.find((item) => item.id === selectedAgent.parentAgentId)
    return <aside className="inspector-panel">
      <div className="inspector-header"><strong>Agent 详情</strong><button type="button" onClick={onCloseAgent}>×</button></div>
      <section><div className="panel-title">Agent</div><div className={`status-line status-${selectedAgent.status}`}>● {selectedAgent.name} · {statusLabels[selectedAgent.status]}</div><div className="panel-value muted">父级：{parent?.name ?? '主 Agent'}</div></section>
      <section><div className="panel-title">任务</div><div className="panel-value">{selectedAgent.task || '未提供任务描述'}</div></section>
      <section><div className="panel-title">当前判断</div><div className="decision-box">{selectedAgent.decisionSummary || '等待结构化思考摘要'}</div></section>
      <section><div className="panel-title">最近动作</div><div className="panel-value">{selectedAgent.currentAction || '暂无'}</div></section>
      <section><div className="panel-title">执行统计</div><div className="panel-value">{tools.length} 次工具调用<br />{tools.filter((item) => item.status === 'completed').length} 次完成</div></section>
      {selectedAgent.result && <section><div className="panel-title">Agent 结果</div><MarkdownContent content={selectedAgent.result} compact /></section>}
    </aside>
  }

  const currentStep = snapshot.plan?.steps[0] ?? null
  return (
    <aside className="inspector-panel">
      <div className="inspector-header"><strong>任务检查器</strong></div>
      <section><div className="panel-title">运行状态</div><div className={`status-line status-${snapshot.run.status}`}>● {statusLabels[snapshot.run.status]}</div></section>
      <section><div className="panel-title">目标</div><div className="panel-value">{snapshot.run.goal}</div></section>
      <section>
        <div className="panel-title">当前任务</div>
        <div className="inspector-current-task">
          <strong>{currentStep?.description ?? snapshot.run.goal}</strong>
          <small>{statusLabels[currentStep?.status ?? snapshot.run.status]}</small>
        </div>
        {snapshot.plan && snapshot.plan.revision > 1 && (
          <div className="plan-revision-note">计划已动态调整 {snapshot.plan.revision - 1} 次，只展示当前第一条任务。</div>
        )}
      </section>
      <section><div className="panel-title">资源</div><div className="panel-value">{snapshot.agents.length} 个 Agent · {snapshot.toolCalls.length} 次工具调用<br />{snapshot.fileChanges.length} 个文件变更</div></section>
      {Object.keys(snapshot.usage).length > 0 && <section><div className="panel-title">用量</div><pre className="compact-pre">{JSON.stringify(snapshot.usage, null, 2)}</pre></section>}
    </aside>
  )
}
