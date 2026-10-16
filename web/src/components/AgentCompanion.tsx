import type { AgentActivity } from '../lib/types'
import { AgentSprite } from './AgentSprite'

const ACTIVITY_DETAILS: Record<AgentActivity['phase'], string> = {
  idle: '分析代码、推进任务，或一起理清一个想法。',
  loading: '正在准备这段对话。',
  queued: '任务已排队，轮到后会继续。',
  thinking: '正在梳理信息，寻找下一步。',
  tool: '正在调用工具，把计划变成行动。',
  subagent: '子任务正在推进，可以展开查看过程。',
  responding: '正在把结果整理成回复。',
  compressing: '正在整理上下文，保留重要信息。',
  completed: '这一轮完成了，随时可以继续。',
  error: '遇到了一点问题，请查看下方提示。',
  background: '其他会话仍在工作，这里可以继续。',
}

/** 根据 ViewModel 呈现伙伴；空会话和工作中共用一个动画实例。 */
export function AgentCompanion({ activity, sessionId, compact }: { activity: AgentActivity; sessionId: string; compact: boolean }) {
  return (
    <section className={`agent-companion ${compact ? 'compact' : 'welcome'}`} aria-label="AI 工作伙伴" data-phase={activity.phase}>
      <div className="companion-stage"><AgentSprite key={sessionId} phase={activity.phase} /></div>
      <div className="companion-copy">
        {!compact && <h1>想法，从这里开始。</h1>}
        <div className={`companion-status ${activity.status}`} role="status"><span className="status-dot" aria-hidden="true" />{activity.label}</div>
        {!compact && <p>{ACTIVITY_DETAILS[activity.phase]}</p>}
      </div>
    </section>
  )
}
