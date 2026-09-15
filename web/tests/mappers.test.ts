import { mapConversation, mapMessage, mapRunSnapshot, mapSettings } from '../src/models/mappers'
import { createSnapshotDto } from './fixtures'

describe('协议 DTO 映射', () => {
  it('把后端运行快照完整映射为前端领域模型', () => {
    const dto = createSnapshotDto({
      run: {
        ...createSnapshotDto().run,
        status: 'running',
        route: 'plan',
        last_seq: 12,
      },
      plan: {
        plan_id: 'plan_1',
        goal: '完成测试',
        project_path: '/project',
        revision: 2,
        steps: [
          {
            step_id: 'step_1',
            description: '编写测试',
            depends_on: [],
            status: 'completed',
            output: '完成',
          },
        ],
      },
      agents: [
        {
          agent_id: 'agent_child',
          parent_agent_id: 'agent_main',
          run_id: 'run_test',
          step_id: 'step_1',
          name: '测试 Agent',
          task: '运行测试',
          status: 'running',
          current_action: '执行 npm test',
          decision_summary: '先测前端再统一回归',
          started_at: '2026-08-18T08:00:01Z',
          completed_at: null,
          result: null,
          error: null,
        },
      ],
      messages: [{ message_id: 'msg_1', content: '执行中', completed: false }],
      file_changes: [{ path: '/project/test.ts', change_type: 'created' }],
      usage: { total_tokens: 20 },
    })

    const snapshot = mapRunSnapshot(dto)

    expect(snapshot.run).toMatchObject({ route: 'plan', status: 'running', lastSeq: 12 })
    expect(snapshot.plan).toMatchObject({ id: 'plan_1', projectPath: '/project', revision: 2 })
    expect(snapshot.plan?.steps[0]).toMatchObject({
      id: 'step_1',
      dependsOn: [],
      status: 'completed',
    })
    expect(snapshot.agents[0]).toMatchObject({
      id: 'agent_child',
      parentAgentId: 'agent_main',
      currentAction: '执行 npm test',
    })
    expect(snapshot.messages[0]).toEqual({ id: 'msg_1', content: '执行中', completed: false })
    expect(snapshot.fileChanges).toEqual([{ path: '/project/test.ts', change_type: 'created' }])
    expect(snapshot.usage).toEqual({ total_tokens: 20 })
  })

  it('规范化多形态消息正文和未知角色', () => {
    const mapped = mapMessage({
      message_id: 'msg_1',
      role: 'unknown',
      content: ['第一段', { text: '第二段' }, { value: 3 }],
    })

    expect(mapped).toEqual({
      id: 'msg_1',
      role: 'assistant',
      content: '第一段\n第二段\n{"value":3}',
    })
  })

  it('映射会话和脱敏设置字段', () => {
    expect(
      mapConversation({
        conversation_id: 'conv_1',
        title: '测试会话',
        workspace_path: '/project',
        created_at: 'created',
        updated_at: 'updated',
      }),
    ).toEqual({
      id: 'conv_1',
      title: '测试会话',
      workspacePath: '/project',
      createdAt: 'created',
      updatedAt: 'updated',
    })
    expect(
      mapSettings({
        provider: 'litellm',
        model: 'openai/test',
        base_url: 'https://example.test',
        has_api_key: true,
        api_key_masked: '••••1234',
        default_workspace: '/project',
        log_level: 'INFO',
        sandbox_enabled: true,
      }),
    ).toEqual({
      provider: 'litellm',
      model: 'openai/test',
      baseUrl: 'https://example.test',
      hasApiKey: true,
      apiKeyMasked: '••••1234',
      defaultWorkspace: '/project',
      logLevel: 'INFO',
      sandboxEnabled: true,
    })
  })
})
