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

  /** 建立新订阅，并隔离已关闭连接的延迟回调。 */
  connect(runId: string, callbacks: EventCallbacks): () => void {
    this.disconnect()
    this.stopped = false
    this.retryCount = 0
    let active = true
    let currentSocket: WebSocket | null = null

    const openSocket = () => {
      if (this.stopped || !active) return
      callbacks.onStateChange('connecting')
      const separator = this.eventsUrl.includes('?') ? '&' : '?'
      const url = `${this.eventsUrl}${separator}token=${encodeURIComponent(this.accessToken)}`
      const socket = new WebSocket(url)
      currentSocket = socket
      this.socket = socket

      socket.onmessage = async (message) => {
        if (!active || this.socket !== socket) return
        try {
          const payload = JSON.parse(String(message.data)) as Record<string, unknown>
          if (!payload || typeof payload !== 'object' || typeof payload.type !== 'string') {
            throw new Error('事件格式错误')
          }
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
          await callbacks.onEvent(payload as EventEnvelopeDto)
        } catch {
          if (active && this.socket === socket) callbacks.onError('处理运行事件失败，请重新加载任务')
        }
      }

      socket.onerror = () => {
        if (active && this.socket === socket) callbacks.onStateChange('error')
      }
      socket.onclose = () => {
        if (this.stopped || !active || this.socket !== socket) return
        callbacks.onStateChange('disconnected')
        const delay = Math.min(1000 * 2 ** this.retryCount, 10000)
        this.retryCount += 1
        this.reconnectTimer = window.setTimeout(openSocket, delay)
      }
    }

    openSocket()
    return () => {
      if (!active) return
      active = false
      if (this.socket === currentSocket) this.disconnect()
    }
  }

  /** 主动关闭连接并停止后续重连。 */
  disconnect(): void {
    this.stopped = true
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    if (this.socket) {
      this.socket.onmessage = null
      this.socket.onerror = null
      this.socket.onclose = null
      this.socket.close()
    }
    this.socket = null
  }
}
