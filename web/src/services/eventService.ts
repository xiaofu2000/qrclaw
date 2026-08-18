import type { EventEnvelopeDto } from '../models/protocol'
import type { ConnectionState } from '../models/workbench'

type EventCallbacks = {
  getAfterSeq: () => number
  onEvent: (event: EventEnvelopeDto) => void | Promise<void>
  onStateChange: (state: ConnectionState) => void
  onError: (message: string) => void
}

/** 管理单个 Run 的 WebSocket 订阅、游标补发和自动重连。 */
export class EventService {
  private socket: WebSocket | null = null
  private stopped = false
  private retryCount = 0
  private reconnectTimer: number | null = null
  private readonly eventsUrl: string
  private readonly accessToken: string

  constructor(eventsUrl: string, accessToken: string) {
    this.eventsUrl = eventsUrl
    this.accessToken = accessToken
  }

  connect(runId: string, callbacks: EventCallbacks): () => void {
    this.stopped = false

    const openSocket = () => {
      if (this.stopped) return
      callbacks.onStateChange('connecting')
      const separator = this.eventsUrl.includes('?') ? '&' : '?'
      const url = `${this.eventsUrl}${separator}token=${encodeURIComponent(this.accessToken)}`
      const socket = new WebSocket(url)
      this.socket = socket

      socket.onmessage = (message) => {
        const payload = JSON.parse(String(message.data)) as Record<string, unknown>
        if (payload.type === 'connection.ready') {
          this.retryCount = 0
          callbacks.onStateChange('connected')
          socket.send(
            JSON.stringify({
              type: 'subscribe',
              runs: [{ run_id: runId, after_seq: callbacks.getAfterSeq() }],
            }),
          )
          return
        }
        if (payload.type === 'subscription.error') {
          callbacks.onError('运行订阅失败，请重新加载任务')
          return
        }
        void callbacks.onEvent(payload as EventEnvelopeDto)
      }

      socket.onerror = () => callbacks.onStateChange('error')
      socket.onclose = () => {
        if (this.stopped) return
        callbacks.onStateChange('disconnected')
        const delay = Math.min(1000 * 2 ** this.retryCount, 10000)
        this.retryCount += 1
        this.reconnectTimer = window.setTimeout(openSocket, delay)
      }
    }

    openSocket()
    return () => this.disconnect()
  }

  /** 主动关闭连接并停止后续重连。 */
  disconnect(): void {
    this.stopped = true
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    this.socket?.close()
    this.socket = null
  }
}
