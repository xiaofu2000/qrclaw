import { applyRunEvent } from '../src/repositories/runProjector'
import { createEvent, createSnapshot } from './fixtures'

describe('运行事件投影', () => {
  it('完成事件用完整正文修正部分流式文本', () => {
    const partial = applyRunEvent(createSnapshot(), createEvent(1, 'assistant.delta', { message_id: 'msg', delta: '部分' }))
    const completed = applyRunEvent(partial, createEvent(2, 'assistant.completed', { message_id: 'msg', content: '完整回复' }))
    expect(completed.messages[0]).toMatchObject({ content: '完整回复', completed: true })
  })

  it('完整投影 Direct 路由下的简单 Agent', () => {
    const initial = createSnapshot()
    let snapshot = applyRunEvent(initial, createEvent(1, 'run.started'))
    snapshot = applyRunEvent(snapshot, createEvent(2, 'route.decided', { route: 'direct' }))
    snapshot = applyRunEvent(
      snapshot,
      createEvent(3, 'agent.started', {
        name: '主 Agent',
        task: '回答简单问题',
        step_id: null,
      }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(4, 'agent.progress', {
        current_action: '正在组织答案',
        decision_summary: '问题无需拆分计划，直接回答',
      }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(5, 'assistant.delta', { message_id: 'msg_1', delta: '测试' }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(6, 'assistant.delta', { message_id: 'msg_1', delta: '完成' }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(7, 'assistant.completed', {
        message_id: 'msg_1',
        content: '测试完成',
      }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(8, 'agent.completed', { result: '测试完成' }),
    )
    snapshot = applyRunEvent(snapshot, createEvent(9, 'run.completed'))

    expect(initial.run.status).toBe('queued')
    expect(initial.agents).toEqual([])
    expect(snapshot.run).toMatchObject({
      route: 'direct',
      status: 'completed',
      lastSeq: 9,
    })
    expect(snapshot.plan).toBeNull()
    expect(snapshot.agents).toEqual([
      expect.objectContaining({
        id: 'agent_main',
        parentAgentId: null,
        name: '主 Agent',
        status: 'completed',
        currentAction: '正在组织答案',
        decisionSummary: '问题无需拆分计划，直接回答',
        result: '测试完成',
      }),
    ])
    expect(snapshot.messages).toEqual([
      { id: 'msg_1', content: '测试完成', completed: true },
    ])
  })

  it('完整投影 Plan 路由、步骤和子 Agent', () => {
    let snapshot = createSnapshot()
    snapshot = applyRunEvent(snapshot, createEvent(1, 'run.started'))
    snapshot = applyRunEvent(snapshot, createEvent(2, 'route.decided', { route: 'plan' }))
    snapshot = applyRunEvent(
      snapshot,
      createEvent(3, 'plan.created', {
        plan_id: 'plan_1',
        goal: '检查并修复项目',
        project_path: '/project',
        revision: 1,
        steps: [
          { step_id: 'step_1', description: '分析项目', depends_on: [] },
          { step_id: 'step_2', description: '验证修复', depends_on: ['step_1'] },
        ],
      }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(4, 'step.started', { step_id: 'step_1' }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(
        5,
        'agent.started',
        { name: '项目分析', task: '检查项目结构', step_id: 'step_1' },
        { agent_id: 'agent_child', parent_agent_id: 'agent_main' },
      ),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(
        6,
        'agent.progress',
        {
          current_action: '正在读取 pyproject.toml',
          decision_summary: '先确认依赖定义，再运行验证命令',
        },
        { agent_id: 'agent_child', parent_agent_id: 'agent_main' },
      ),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(7, 'step.completed', { step_id: 'step_1', output: '分析完成' }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(8, 'plan.updated', {
        plan_id: 'plan_1',
        goal: '检查并修复项目',
        project_path: '/project',
        revision: 2,
        steps: [
          { step_id: 'step_1', description: '分析项目', depends_on: [] },
          { step_id: 'step_2', description: '验证修复', depends_on: ['step_1'] },
          { step_id: 'step_3', description: '补充回归', depends_on: ['step_2'] },
        ],
      }),
    )

    expect(snapshot.run.route).toBe('plan')
    expect(snapshot.plan).toMatchObject({
      id: 'plan_1',
      revision: 2,
      projectPath: '/project',
    })
    expect(snapshot.plan?.steps).toEqual([
      expect.objectContaining({ id: 'step_1', status: 'completed', output: '分析完成' }),
      expect.objectContaining({ id: 'step_2', status: 'pending', dependsOn: ['step_1'] }),
      expect.objectContaining({ id: 'step_3', status: 'pending', dependsOn: ['step_2'] }),
    ])
    expect(snapshot.agents[0]).toMatchObject({
      id: 'agent_child',
      parentAgentId: 'agent_main',
      stepId: 'step_1',
      currentAction: '正在读取 pyproject.toml',
      decisionSummary: '先确认依赖定义，再运行验证命令',
    })
  })

  it('plan.created 保留后端提供的当前任务状态', () => {
    const snapshot = applyRunEvent(
      createSnapshot(),
      createEvent(1, 'plan.created', {
        plan_id: 'plan_status',
        goal: '验证状态',
        project_path: '/project',
        revision: 1,
        steps: [
          {
            step_id: 'step_current',
            description: '当前任务',
            depends_on: [],
            status: 'running',
            output: null,
          },
        ],
      }),
    )

    expect(snapshot.plan?.steps[0].status).toBe('running')
  })

  it('归并工具授权、文件变化和用量事件', () => {
    const agent = {
      id: 'agent_main',
      parentAgentId: null,
      runId: 'run_test',
      stepId: null,
      name: '主 Agent',
      task: '执行命令',
      status: 'running' as const,
      currentAction: '',
      decisionSummary: '',
      startedAt: '2026-08-18T08:00:00Z',
      completedAt: null,
      result: null,
      error: null,
    }
    let snapshot = createSnapshot({ agents: [agent] })
    snapshot = applyRunEvent(
      snapshot,
      createEvent(1, 'tool.approval_required', {
        approval_id: 'approval_1',
        tool_call_id: 'tool_1',
        tool_name: 'run_shell',
        arguments: { command: 'npm test' },
        purpose: '执行测试',
      }),
    )

    expect(snapshot.agents[0].status).toBe('waiting_approval')
    expect(snapshot.pendingApprovals).toHaveLength(1)
    expect(snapshot.toolCalls[0]).toMatchObject({
      id: 'tool_1',
      status: 'waiting_approval',
    })

    snapshot = applyRunEvent(
      snapshot,
      createEvent(2, 'tool.approval_resolved', {
        approval_id: 'approval_1',
        decision: 'allow_once',
      }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(3, 'tool.started', {
        tool_call_id: 'tool_1',
        name: 'run_shell',
        arguments: { command: 'npm test' },
      }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(4, 'tool.completed', { tool_call_id: 'tool_1', result: '通过' }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(5, 'file.changed', { path: '/project/a.ts', change_type: 'modified' }),
    )
    snapshot = applyRunEvent(
      snapshot,
      createEvent(6, 'usage.updated', { total_tokens: 32, duration_ms: 120 }),
    )

    expect(snapshot.pendingApprovals).toEqual([])
    expect(snapshot.agents[0].status).toBe('running')
    expect(snapshot.toolCalls[0]).toMatchObject({ status: 'completed', result: '通过' })
    expect(snapshot.fileChanges).toEqual([
      { path: '/project/a.ts', change_type: 'modified' },
    ])
    expect(snapshot.usage).toEqual({ total_tokens: 32, duration_ms: 120 })
  })

  it('忽略重复和过期事件', () => {
    const current = createSnapshot({
      run: { ...createSnapshot().run, status: 'completed', lastSeq: 8 },
    })
    const result = applyRunEvent(current, createEvent(7, 'run.failed', { message: '旧错误' }))

    expect(result).toBe(current)
    expect(result.run.status).toBe('completed')
    expect(result.run.error).toBeNull()
  })

  it.each([
    ['run.failed', 'failed'],
    ['run.cancelled', 'cancelled'],
  ] as const)('%s 会收敛仍在活动中的子项', (eventType, expectedStatus) => {
    const snapshot = createSnapshot({
      agents: [
        {
          id: 'agent_main',
          parentAgentId: null,
          runId: 'run_test',
          stepId: null,
          name: '主 Agent',
          task: '执行任务',
          status: 'running',
          currentAction: '',
          decisionSummary: '',
          startedAt: null,
          completedAt: null,
          result: null,
          error: null,
        },
      ],
      toolCalls: [
        {
          id: 'tool_1',
          runId: 'run_test',
          agentId: 'agent_main',
          name: 'run_shell',
          arguments: {},
          status: 'running',
          startedAt: null,
          completedAt: null,
          result: null,
          error: null,
        },
      ],
    })

    const result = applyRunEvent(snapshot, createEvent(1, eventType, { message: '结束' }))

    expect(result.run.status).toBe(expectedStatus)
    expect(result.agents[0].status).toBe(expectedStatus)
    expect(result.toolCalls[0].status).toBe(expectedStatus)
  })
})
