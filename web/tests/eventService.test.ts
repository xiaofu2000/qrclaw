import type { EventEnvelopeDto } from '../src/models/protocol'
import type { ConnectionState } from '../src/models/workbench'
import { EventService } from '../src/services/eventService'
import { createEvent } from './fixtures'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []

  readonly url: string
  readonly sent: string[] = []
  closed = false
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor(url: string | URL) {
    this.url = String(url)
    FakeWebSocket.instances.push(this)
  }

  /** 记录客户端发送给服务端的订阅消息。 */
  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    this.sent.push(String(data))
  }

  /** 标记连接已被客户端主动关闭。 */
  close(): void {
    this.closed = true
  }
}

/** 向假 WebSocket 注入一条服务端消息。 */
function receive(socket: FakeWebSocket, payload: Record<string, unknown>): void {
  socket.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent)
}

describe('EventService', () => {
  it('无效事件显示中文错误，后续正常事件仍可处理', async () => {
    const onError = vi.fn()
    const onEvent = vi.fn()
    const service = new EventService('ws://localhost/events', 'token')
    service.connect('run_test', { getAfterSeq: () => 0, onEvent, onStateChange: vi.fn(), onError })
    const socket = FakeWebSocket.instances[0]
    socket.onmessage?.({ data: 'invalid JSON' } as MessageEvent)
    receive(socket, createEvent(1, 'run.started'))
    await Promise.resolve()
    expect(onError).toHaveBeenCalledWith('处理运行事件失败，请重新加载任务')
    expect(onEvent).toHaveBeenCalledOnce()
  })

  it('旧连接延迟关闭或重复清理时，不影响新任务连接', () => {
    vi.useFakeTimers()
    const service = new EventService('ws://localhost/events', 'token')
    const callbacks = { getAfterSeq: () => 0, onEvent: vi.fn(), onStateChange: vi.fn(), onError: vi.fn() }
    const stopOld = service.connect('old', callbacks)
    const oldSocket = FakeWebSocket.instances[0]
    const delayedClose = oldSocket.onclose
    stopOld()
    service.connect('new', callbacks)
    const newSocket = FakeWebSocket.instances[1]
    delayedClose?.(new CloseEvent('close'))
    stopOld()
    vi.advanceTimersByTime(20_000)
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(newSocket.closed).toBe(false)
  })

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('连接就绪后携带最新游标订阅运行', async () => {
    const states: ConnectionState[] = []
    const received: EventEnvelopeDto[] = []
    const service = new EventService('ws://127.0.0.1/api/v1/events', 'token /?')

    const disconnect = service.connect('run_test', {
      getAfterSeq: () => 12,
      onEvent: (event) => {
        received.push(event)
      },
      onStateChange: (state) => states.push(state),
      onError: vi.fn(),
    })
    const socket = FakeWebSocket.instances[0]
    receive(socket, { type: 'connection.ready', version: '1' })
    receive(socket, createEvent(13, 'agent.progress', { current_action: '继续执行' }))
    await Promise.resolve()

    expect(socket.url).toBe('ws://127.0.0.1/api/v1/events?token=token%20%2F%3F')
    expect(states).toEqual(['connecting', 'connected'])
    expect(JSON.parse(socket.sent[0])).toEqual({
      type: 'subscribe',
      runs: [{ run_id: 'run_test', after_seq: 12 }],
    })
    expect(received).toEqual([
      expect.objectContaining({ seq: 13, type: 'agent.progress', run_id: 'run_test' }),
    ])

    disconnect()
    expect(socket.closed).toBe(true)
  })

  it('把订阅错误转换为用户可理解的提示', () => {
    const onError = vi.fn()
    const service = new EventService('ws://localhost/events?client=web', 'token')
    service.connect('run_missing', {
      getAfterSeq: () => 0,
      onEvent: vi.fn(),
      onStateChange: vi.fn(),
      onError,
    })

    const socket = FakeWebSocket.instances[0]
    receive(socket, { type: 'subscription.error', code: 'run_not_found' })

    expect(socket.url).toBe('ws://localhost/events?client=web&token=token')
    expect(onError).toHaveBeenCalledWith('运行订阅失败，请重新加载任务')
  })

  it('异常断开后按退避时间重连，主动断开后停止重连', () => {
    vi.useFakeTimers()
    const states: ConnectionState[] = []
    const service = new EventService('ws://localhost/events', 'token')
    const disconnect = service.connect('run_test', {
      getAfterSeq: () => 0,
      onEvent: vi.fn(),
      onStateChange: (state) => states.push(state),
      onError: vi.fn(),
    })

    const first = FakeWebSocket.instances[0]
    first.onclose?.(new CloseEvent('close'))
    expect(states).toEqual(['connecting', 'disconnected'])

    vi.advanceTimersByTime(999)
    expect(FakeWebSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(states.at(-1)).toBe('connecting')

    disconnect()
    vi.advanceTimersByTime(20_000)
    expect(FakeWebSocket.instances).toHaveLength(2)
  })
})
