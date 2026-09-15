import { mapRunSnapshot } from '../models/mappers'
import type { EventEnvelopeDto } from '../models/protocol'
import type { ConnectionState, RunSnapshot } from '../models/workbench'
import { ApiService } from '../services/apiService'
import { EventService } from '../services/eventService'
import { applyRunEvent } from './runProjector'

type RunListener = (snapshot: RunSnapshot) => void
type ConnectionListener = (state: ConnectionState) => void
type ErrorListener = (message: string) => void

/** 负责运行快照加载、事件去重、缺口恢复与实时订阅。 */
export class RunRepository {
  private snapshot: RunSnapshot | null = null
  private disconnectEvents: (() => void) | null = null
  private readonly api: ApiService
  private readonly events: EventService

  constructor(api: ApiService, events: EventService) {
    this.api = api
    this.events = events
  }

  /** 加载指定运行的权威快照。 */
  async load(runId: string): Promise<RunSnapshot> {
    this.snapshot = mapRunSnapshot(await this.api.getRun(runId))
    return this.snapshot
  }

  /** 使用幂等请求标识启动运行，然后读取快照。 */
  async start(conversationId: string, content: string): Promise<RunSnapshot> {
    const result = await this.api.createRun(
      conversationId,
      content,
      `client_${crypto.randomUUID()}`,
    )
    return this.load(result.run_id)
  }

  /** 订阅运行事件，按顺序处理并隔离旧订阅的回调。 */
  subscribe(
    runId: string,
    onRunChange: RunListener,
    onConnectionChange: ConnectionListener,
    onError: ErrorListener,
  ): () => void {
    this.disconnectEvents?.()
    let active = true
    let pending = Promise.resolve()
    const disconnect = this.events.connect(runId, {
      getAfterSeq: () => this.snapshot?.run.lastSeq ?? 0,
      onStateChange: onConnectionChange,
      onError,
      onEvent: (event) => {
        // 串行处理恢复期间收到的事件，避免丢弃比快照更新的消息。
        pending = pending.then(async () => {
          if (active) await this.mergeEvent(event, (snapshot) => {
            if (active) onRunChange(snapshot)
          }, (message) => {
            if (active) onError(message)
          })
        }).catch((error: unknown) => {
          if (active) onError(error instanceof Error ? error.message : '处理运行事件失败')
        })
        return pending
      },
    })
    const unsubscribe = () => {
      active = false
      disconnect()
      if (this.disconnectEvents === unsubscribe) this.disconnectEvents = null
    }
    this.disconnectEvents = unsubscribe
    return unsubscribe
  }

  async cancel(): Promise<void> {
    if (!this.snapshot) return
    await this.api.cancelRun(this.snapshot.run.id)
  }

  async resolveApproval(approvalId: string, decision: 'allow_once' | 'deny'): Promise<void> {
    await this.api.resolveApproval(approvalId, decision)
  }

  /** 遇到乱序或序号缺口时直接重新获取权威快照。 */
  private async mergeEvent(
    event: EventEnvelopeDto,
    onRunChange: RunListener,
    onError: ErrorListener,
  ): Promise<void> {
    if (!this.snapshot || event.run_id !== this.snapshot.run.id) return
    if (event.seq <= this.snapshot.run.lastSeq) return
    if (event.seq > this.snapshot.run.lastSeq + 1) {
      try {
        const snapshot = mapRunSnapshot(await this.api.getRun(event.run_id))
        if (this.snapshot?.run.id !== event.run_id) return
        if (snapshot.run.lastSeq < this.snapshot.run.lastSeq) return
        this.snapshot = snapshot
        onRunChange(snapshot)
      } catch (error) {
        onError(error instanceof Error ? error.message : '恢复运行快照失败')
      }
      return
    }
    this.snapshot = applyRunEvent(this.snapshot, event)
    onRunChange(this.snapshot)
  }
}
