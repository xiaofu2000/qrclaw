import type {
  ApiErrorResponse,
  ApiResponse,
  CapabilitiesDto,
  ConversationDto,
  MessageDto,
  RunSnapshotDto,
  SettingsDto,
} from '../models/protocol'
import type { SettingsInput } from '../models/workbench'

/** 带稳定错误码的 HTTP 请求错误。 */
export class ApiError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly status: number

  constructor(
    code: string,
    message: string,
    retryable = false,
    status = 0,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.retryable = retryable
    this.status = status
  }
}

/** 封装 QRClaw HTTP 协议，View 不应直接使用 fetch。 */
export class ApiService {
  private readonly baseUrl: string
  private readonly accessToken: string

  constructor(baseUrl: string, accessToken: string) {
    this.baseUrl = baseUrl
    this.accessToken = accessToken
  }

  async health(): Promise<{ status: string; service: string }> {
    return this.request('/health', { authenticated: false })
  }

  async capabilities(): Promise<CapabilitiesDto> {
    return this.request('/capabilities')
  }

  async listConversations(): Promise<ConversationDto[]> {
    return this.request('/conversations')
  }

  async createConversation(title: string, workspacePath: string): Promise<ConversationDto> {
    return this.request('/conversations', {
      method: 'POST',
      body: { title, workspace_path: workspacePath },
    })
  }

  async updateConversation(conversationId: string, title: string): Promise<ConversationDto> {
    return this.request(`/conversations/${encodeURIComponent(conversationId)}`, {
      method: 'PATCH',
      body: { title },
    })
  }

  async deleteConversation(conversationId: string): Promise<void> {
    await this.request(`/conversations/${encodeURIComponent(conversationId)}`, {
      method: 'DELETE',
    })
  }

  async getMessages(conversationId: string): Promise<MessageDto[]> {
    return this.request(`/conversations/${encodeURIComponent(conversationId)}/messages`)
  }

  async createRun(
    conversationId: string,
    content: string,
    clientRequestId: string,
  ): Promise<{ run_id: string; status: string }> {
    return this.request('/runs', {
      method: 'POST',
      body: {
        conversation_id: conversationId,
        content,
        client_request_id: clientRequestId,
      },
    })
  }

  async getRun(runId: string): Promise<RunSnapshotDto> {
    return this.request(`/runs/${encodeURIComponent(runId)}`)
  }

  async cancelRun(runId: string): Promise<void> {
    await this.request(`/runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
      body: { reason: 'user_requested' },
    })
  }

  async resolveApproval(approvalId: string, decision: 'allow_once' | 'deny'): Promise<void> {
    await this.request(`/tool-approvals/${encodeURIComponent(approvalId)}/resolve`, {
      method: 'POST',
      body: { decision },
    })
  }

  async getSettings(): Promise<SettingsDto> {
    return this.request('/settings')
  }

  async updateSettings(input: SettingsInput): Promise<SettingsDto> {
    return this.request('/settings', {
      method: 'PUT',
      body: {
        provider: input.provider,
        model: input.model,
        base_url: input.baseUrl,
        api_key: input.apiKey,
        default_workspace: input.defaultWorkspace,
        log_level: input.logLevel,
      },
    })
  }

  async testConnection(): Promise<{ connected: boolean; response: string }> {
    return this.request('/settings/test-connection', { method: 'POST' })
  }

  /** 执行统一请求并把协议错误转换为 ApiError。 */
  private async request<T>(
    path: string,
    options: {
      method?: string
      body?: Record<string, unknown>
      authenticated?: boolean
    } = {},
  ): Promise<T> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Request-ID': `web_${crypto.randomUUID()}`,
    }
    if (options.authenticated !== false && this.accessToken) {
      headers['X-QRClaw-Token'] = this.accessToken
    }

    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method: options.method ?? 'GET',
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
      })
    } catch {
      throw new ApiError('network_error', '无法连接本地服务', true)
    }

    if (response.status === 204) return undefined as T
    const payload = (await response.json().catch(() => null)) as
      | ApiResponse<T>
      | ApiErrorResponse
      | null
    if (!response.ok || !payload || 'error' in payload) {
      const error = payload && 'error' in payload ? payload.error : null
      throw new ApiError(
        error?.code ?? 'invalid_response',
        error?.message ?? `本地服务返回异常状态 ${response.status}`,
        error?.retryable ?? false,
        response.status,
      )
    }
    return payload.data
  }
}
