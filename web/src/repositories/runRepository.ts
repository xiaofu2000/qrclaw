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
  private syncing = false
  private readonly api: ApiService
  private readonly events: EventService

  constructor(api: ApiService, events: EventService) {
    this.api = api
    this.events = events
  }

  async load(runId: string): Promise<RunSnapshot> {
    this.snapshot = mapRunSnapshot(await this.api.getRun(runId))
    return this.snapshot
  }

  async start(conversationId: string, content: string): Promise<RunSnapshot> {
    const result = await this.api.createRun(
      conversationId,
      content,
      `client_${crypto.randomUUID()}`,
    )
    return this.load(result.run_id)
  }

  subscribe(
    runId: string,
    onRunChange: RunListener,
    onConnectionChange: ConnectionListener,
    onError: ErrorListener,
  ): () => void {
    this.disconnectEvents?.()
    this.disconnectEvents = this.events.connect(runId, {
      getAfterSeq: () => this.snapshot?.run.lastSeq ?? 0,
      onStateChange: onConnectionChange,
      onError,
      onEvent: async (event) => {
        await this.mergeEvent(event, onRunChange, onError)
      },
    })
    return () => {
      this.disconnectEvents?.()
      this.disconnectEvents = null
    }
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
      if (this.syncing) return
      this.syncing = true
      try {
        await this.load(event.run_id)
        if (this.snapshot) onRunChange(this.snapshot)
      } catch (error) {
        onError(error instanceof Error ? error.message : '恢复运行快照失败')
      } finally {
        this.syncing = false
      }
      return
    }
    this.snapshot = applyRunEvent(this.snapshot, event)
    onRunChange(this.snapshot)
  }
}
