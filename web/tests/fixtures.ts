import type { EventEnvelopeDto, RunSnapshotDto } from '../src/models/protocol'
import type { RunSnapshot } from '../src/models/workbench'

/** 创建领域层使用的空运行快照。 */
export function createSnapshot(overrides: Partial<RunSnapshot> = {}): RunSnapshot {
  return {
    run: {
      id: 'run_test',
      conversationId: 'conv_test',
      status: 'queued',
      route: null,
      goal: '完成测试任务',
      createdAt: '2026-08-18T08:00:00Z',
      updatedAt: '2026-08-18T08:00:00Z',
      lastSeq: 0,
      error: null,
    },
    plan: null,
    agents: [],
    toolCalls: [],
    pendingApprovals: [],
    messages: [],
    fileChanges: [],
    usage: {},
    ...overrides,
  }
}

/** 创建后端协议格式的空运行快照。 */
export function createSnapshotDto(overrides: Partial<RunSnapshotDto> = {}): RunSnapshotDto {
  return {
    run: {
      run_id: 'run_test',
      conversation_id: 'conv_test',
      status: 'queued',
      route: null,
      goal: '完成测试任务',
      created_at: '2026-08-18T08:00:00Z',
      updated_at: '2026-08-18T08:00:00Z',
      last_seq: 0,
      error: null,
    },
    plan: null,
    agents: [],
    tool_calls: [],
    pending_approvals: [],
    messages: [],
    file_changes: [],
    usage: {},
    ...overrides,
  }
}

/** 创建单次运行的结构化事件。 */
export function createEvent(
  seq: number,
  type: string,
  data: Record<string, unknown> = {},
  overrides: Partial<EventEnvelopeDto> = {},
): EventEnvelopeDto {
  return {
    version: '1',
    event_id: `evt_${seq}`,
    seq,
    timestamp: `2026-08-18T08:00:${String(seq).padStart(2, '0')}Z`,
    conversation_id: 'conv_test',
    run_id: 'run_test',
    agent_id: 'agent_main',
    parent_agent_id: null,
    type,
    data,
    ...overrides,
  }
}
