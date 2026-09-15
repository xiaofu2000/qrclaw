import type { EventEnvelopeDto } from '../src/models/protocol'
import type { ConnectionState, RunSnapshot } from '../src/models/workbench'
import { RunRepository } from '../src/repositories/runRepository'
import type { ApiService } from '../src/services/apiService'
import type { EventService } from '../src/services/eventService'
import { createEvent, createSnapshotDto } from './fixtures'

type TestEventCallbacks = {
  getAfterSeq: () => number
  onEvent: (event: EventEnvelopeDto) => void | Promise<void>
  onStateChange: (state: ConnectionState) => void
  onError: (message: string) => void
}

/** 创建可控制 API 与事件回调的运行仓库。 */
function createRepository() {
  let callbacks: TestEventCallbacks | null = null
  const disconnect = vi.fn()
  const api = {
    getRun: vi.fn().mockResolvedValue(createSnapshotDto()),
    createRun: vi.fn().mockResolvedValue({ run_id: 'run_test', status: 'queued' }),
    cancelRun: vi.fn().mockResolvedValue(undefined),
    resolveApproval: vi.fn().mockResolvedValue(undefined),
  }
  const events = {
    connect: vi.fn((_runId: string, nextCallbacks: TestEventCallbacks) => {
      callbacks = nextCallbacks
      return disconnect
    }),
  }
  const repository = new RunRepository(
    api as unknown as ApiService,
    events as unknown as EventService,
  )
  return {
    repository,
    api,
    events,
    disconnect,
    getCallbacks: () => callbacks,
  }
}

describe('RunRepository', () => {
  it('恢复快照期间到达的新事件不会丢失', async () => {
    const { repository, api, getCallbacks } = createRepository()
    const onChange = vi.fn()
    await repository.load('run_test')
    let resolveSnapshot!: (snapshot: ReturnType<typeof createSnapshotDto>) => void
    api.getRun.mockReturnValueOnce(new Promise((resolve) => { resolveSnapshot = resolve }))
    repository.subscribe('run_test', onChange, vi.fn(), vi.fn())
    const first = getCallbacks()?.onEvent(createEvent(3, 'run.started'))
    await Promise.resolve()
    const second = getCallbacks()?.onEvent(createEvent(4, 'run.completed'))
    resolveSnapshot(createSnapshotDto({ run: { ...createSnapshotDto().run, status: 'running', last_seq: 3 } }))
    await Promise.all([first, second])
    expect(onChange.mock.calls.at(-1)?.[0].run).toMatchObject({ lastSeq: 4, status: 'completed' })
  })

  it('启动运行时生成客户端幂等 ID 并加载权威快照', async () => {
    const { repository, api } = createRepository()

    const snapshot = await repository.start('conv_test', '执行简单任务')

    expect(api.createRun).toHaveBeenCalledWith(
      'conv_test',
      '执行简单任务',
      expect.stringMatching(/^client_/),
    )
    expect(api.getRun).toHaveBeenCalledWith('run_test')
    expect(snapshot.run.id).toBe('run_test')
  })

  it('按序归并 Direct 路由事件并通知 ViewModel', async () => {
    const { repository, events, getCallbacks } = createRepository()
    const changes: RunSnapshot[] = []
    await repository.load('run_test')
    const unsubscribe = repository.subscribe(
      'run_test',
      (snapshot) => changes.push(snapshot),
      vi.fn(),
      vi.fn(),
    )
    const callbacks = getCallbacks()
    expect(callbacks).not.toBeNull()

    await callbacks?.onEvent(createEvent(1, 'run.started'))
    await callbacks?.onEvent(createEvent(2, 'route.decided', { route: 'direct' }))

    expect(events.connect).toHaveBeenCalledWith('run_test', expect.any(Object))
    expect(callbacks?.getAfterSeq()).toBe(2)
    expect(changes).toHaveLength(2)
    expect(changes.at(-1)?.run.route).toBe('direct')
    unsubscribe()
  })

  it('忽略重复事件和其他运行的事件', async () => {
    const { repository, getCallbacks } = createRepository()
    const onChange = vi.fn()
    await repository.load('run_test')
    repository.subscribe('run_test', onChange, vi.fn(), vi.fn())
    const callbacks = getCallbacks()

    await callbacks?.onEvent(createEvent(1, 'run.started'))
    await callbacks?.onEvent(createEvent(1, 'run.failed'))
    await callbacks?.onEvent(
      createEvent(2, 'run.completed', {}, { run_id: 'run_other' }),
    )

    expect(onChange).toHaveBeenCalledOnce()
    expect(callbacks?.getAfterSeq()).toBe(1)
  })

  it('发现事件序号缺口时重新加载后端快照', async () => {
    const { repository, api, getCallbacks } = createRepository()
    const onChange = vi.fn()
    const onError = vi.fn()
    await repository.load('run_test')
    api.getRun.mockResolvedValueOnce(
      createSnapshotDto({
        run: {
          ...createSnapshotDto().run,
          status: 'running',
          route: 'plan',
          last_seq: 4,
        },
      }),
    )
    repository.subscribe('run_test', onChange, vi.fn(), onError)

    await getCallbacks()?.onEvent(createEvent(4, 'plan.created'))

    expect(api.getRun).toHaveBeenCalledTimes(2)
    expect(onChange).toHaveBeenCalledOnce()
    expect(onChange.mock.calls[0][0].run).toMatchObject({ route: 'plan', lastSeq: 4 })
    expect(onError).not.toHaveBeenCalled()
  })

  it('快照恢复失败时上报中文错误', async () => {
    const { repository, api, getCallbacks } = createRepository()
    const onError = vi.fn()
    await repository.load('run_test')
    api.getRun.mockRejectedValueOnce(new Error('服务不可用'))
    repository.subscribe('run_test', vi.fn(), vi.fn(), onError)

    await getCallbacks()?.onEvent(createEvent(3, 'run.completed'))

    expect(onError).toHaveBeenCalledWith('服务不可用')
  })

  it('取消和授权命令委托给 HTTP 服务', async () => {
    const { repository, api } = createRepository()
    await repository.load('run_test')

    await repository.cancel()
    await repository.resolveApproval('approval_1', 'allow_once')

    expect(api.cancelRun).toHaveBeenCalledWith('run_test')
    expect(api.resolveApproval).toHaveBeenCalledWith('approval_1', 'allow_once')
  })
})
