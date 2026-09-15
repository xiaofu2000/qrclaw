import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolApprovalDialog } from '../src/features/approvals/ToolApprovalDialog'
import { ChatPanel } from '../src/features/chat/ChatPanel'
import { InspectorPanel } from '../src/features/inspector/InspectorPanel'
import { TimelinePanel } from '../src/features/timeline/TimelinePanel'
import type { Agent, Approval } from '../src/models/workbench'
import { createSnapshot } from './fixtures'

/** 创建页面测试使用的 Agent。 */
function createAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'agent_main',
    parentAgentId: null,
    runId: 'run_test',
    stepId: null,
    name: '主 Agent',
    task: '回答简单问题',
    status: 'running',
    currentAction: '正在组织答案',
    decisionSummary: '无需计划，直接回答',
    startedAt: '2026-08-18T08:00:00Z',
    completedAt: null,
    result: null,
    error: null,
    ...overrides,
  }
}

describe('Direct 与 Plan 页面模式', () => {
  it('Direct 模式展示简单 Agent 并允许选择详情', async () => {
    const user = userEvent.setup()
    const onSelectAgent = vi.fn()
    const snapshot = createSnapshot({
      run: {
        ...createSnapshot().run,
        route: 'direct',
        status: 'running',
        goal: '解释项目结构',
      },
      agents: [createAgent()],
    })

    render(
      <TimelinePanel
        snapshot={snapshot}
        selectedAgent={null}
        onSelectAgent={onSelectAgent}
      />,
    )

    expect(screen.getByText('直接执行')).toBeInTheDocument()
    expect(screen.getByText('当前任务')).toBeInTheDocument()
    expect(screen.getByText('主 Agent')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /主 Agent/ }))
    expect(onSelectAgent).toHaveBeenCalledWith('agent_main')
  })

  it('Plan 模式只展示动态计划的第一条当前任务', () => {
    const childAgent = createAgent({
      id: 'agent_child',
      parentAgentId: 'agent_main',
      stepId: 'step_1',
      name: '测试 Agent',
      task: '运行全量测试',
      currentAction: '正在运行 npm test',
    })
    const snapshot = createSnapshot({
      run: {
        ...createSnapshot().run,
        route: 'plan',
        status: 'running',
        goal: '完成前后端测试',
      },
      plan: {
        id: 'plan_1',
        goal: '完成前后端测试',
        projectPath: '/project',
        revision: 2,
        steps: [
          {
            id: 'step_1',
            description: '编写测试',
            dependsOn: [],
            status: 'running',
            output: null,
          },
          {
            id: 'step_2',
            description: '执行全量测试',
            dependsOn: ['step_1'],
            status: 'pending',
            output: null,
          },
        ],
      },
      agents: [childAgent],
    })

    const { rerender } = render(
      <TimelinePanel snapshot={snapshot} selectedAgent={null} onSelectAgent={vi.fn()} />,
    )

    expect(screen.getByText('动态任务')).toBeInTheDocument()
    expect(screen.getByText('编写测试')).toBeInTheDocument()
    expect(screen.getByText('计划已动态调整 1 次')).toBeInTheDocument()
    expect(screen.queryByText('执行全量测试')).not.toBeInTheDocument()
    expect(screen.getByText('测试 Agent')).toBeInTheDocument()

    rerender(
      <InspectorPanel snapshot={snapshot} selectedAgent={null} onCloseAgent={vi.fn()} />,
    )
    expect(screen.getByText('编写测试')).toBeInTheDocument()
    expect(screen.getByText('计划已动态调整 1 次，只展示当前第一条任务。')).toBeInTheDocument()
    expect(screen.queryByText('执行全量测试')).not.toBeInTheDocument()
    expect(screen.getByText('资源').closest('section')).toHaveTextContent(
      '1 个 Agent · 0 次工具调用',
    )
  })

  it('执行过程位于用户任务之后、Markdown 最终回答之前', () => {
    const snapshot = createSnapshot({
      run: {
        ...createSnapshot().run,
        route: 'direct',
        status: 'completed',
        goal: '整理检查结果',
      },
      agents: [createAgent({ status: 'completed' })],
      toolCalls: [
        {
          id: 'tool_1',
          runId: 'run_test',
          agentId: 'agent_main',
          name: 'read_file',
          arguments: { path: 'README.md' },
          status: 'completed',
          startedAt: '2026-08-18T08:00:01Z',
          completedAt: '2026-08-18T08:00:02Z',
          result: '读取完成',
          error: null,
        },
      ],
    })

    render(
      <ChatPanel
        messages={[
          { id: 'user_1', role: 'user', content: '整理检查结果' },
          {
            id: 'assistant_1',
            role: 'assistant',
            content: '# 执行完成\n\n- 已读取文件\n- 已整理结果\n\n| 项目 | 状态 |\n| --- | --- |\n| README | 完成 |',
          },
        ]}
        snapshot={snapshot}
        selectedAgent={null}
        onSelectAgent={vi.fn()}
      />,
    )

    const userTask = screen.getByText('整理检查结果', { selector: '.message-bubble' })
    const execution = screen.getByLabelText('当前任务执行过程')
    const finalAnswer = screen.getByRole('heading', { name: '执行完成' })

    expect(userTask.compareDocumentPosition(execution) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(execution.compareDocumentPosition(finalAnswer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('read_file')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('刷新后历史消息缺少用户任务时仍保持任务、执行过程、回答的顺序', () => {
    const snapshot = createSnapshot({
      run: {
        ...createSnapshot().run,
        route: 'direct',
        status: 'completed',
        goal: '刷新后恢复任务',
      },
    })

    render(
      <ChatPanel
        messages={[{ id: 'assistant_1', role: 'assistant', content: '## 恢复完成' }]}
        snapshot={snapshot}
        selectedAgent={null}
        onSelectAgent={vi.fn()}
      />,
    )

    const restoredTask = screen.getByText('刷新后恢复任务', { selector: '.message-bubble' })
    const execution = screen.getByLabelText('当前任务执行过程')
    const finalAnswer = screen.getByRole('heading', { name: '恢复完成' })

    expect(restoredTask.compareDocumentPosition(execution) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(execution.compareDocumentPosition(finalAnswer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('子 Agent 详情展示父级、判断摘要和工具统计', () => {
    const parentAgent = createAgent()
    const childAgent = createAgent({
      id: 'agent_child',
      parentAgentId: 'agent_main',
      name: '依赖分析',
      task: '检查锁文件',
      decisionSummary: '依赖声明发生变化，需要核对锁文件',
    })
    const snapshot = createSnapshot({
      agents: [parentAgent, childAgent],
      toolCalls: [
        {
          id: 'tool_1',
          runId: 'run_test',
          agentId: 'agent_child',
          name: 'read_file',
          arguments: { path: 'uv.lock' },
          status: 'completed',
          startedAt: null,
          completedAt: null,
          result: '读取完成',
          error: null,
        },
      ],
    })

    render(
      <InspectorPanel
        snapshot={snapshot}
        selectedAgent={childAgent}
        onCloseAgent={vi.fn()}
      />,
    )

    expect(screen.getByText('父级：主 Agent')).toBeInTheDocument()
    expect(screen.getByText('检查锁文件')).toBeInTheDocument()
    expect(screen.getByText('依赖声明发生变化，需要核对锁文件')).toBeInTheDocument()
    expect(screen.getByText(/1 次工具调用/)).toBeInTheDocument()
    expect(screen.getByText(/1 次完成/)).toBeInTheDocument()
  })
})

describe('工具授权交互', () => {
  const approval: Approval = {
    id: 'approval_1',
    toolCallId: 'tool_1',
    runId: 'run_test',
    agentId: 'agent_main',
    createdAt: '2026-08-18T08:00:00Z',
    details: {
      tool_name: 'run_shell',
      purpose: '执行前端测试',
      cwd: '/project',
      arguments: { command: 'npm test' },
      sandbox: { enabled: true, network: 'none' },
      risk_level: 'medium',
    },
  }

  it.each([
    ['拒绝', 'deny'],
    ['允许一次', 'allow_once'],
  ] as const)('点击“%s”只上报对应决定', async (buttonName, decision) => {
    const user = userEvent.setup()
    const onResolve = vi.fn()
    render(
      <ToolApprovalDialog
        approval={approval}
        agent={createAgent()}
        pending={false}
        onResolve={onResolve}
      />,
    )

    expect(screen.getByText('沙箱 · 网络 none')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: buttonName }))

    expect(onResolve).toHaveBeenCalledOnce()
    expect(onResolve).toHaveBeenCalledWith(decision)
  })

  it('请求处理中禁用两个决定按钮', () => {
    render(
      <ToolApprovalDialog
        approval={approval}
        agent={createAgent()}
        pending
        onResolve={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: '拒绝' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '允许一次' })).toBeDisabled()
  })
})
