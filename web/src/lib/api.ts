import type { SessionDetail, SessionEvent, SessionSummary } from './types'
import type { WikiDetail, WikiDraft, WikiLintReport, WikiPageList, WikiStatus } from '../features/memory/types'

const baseUrl = import.meta.env.VITE_QRCLAW_API_BASE_URL ?? (import.meta.env.DEV ? '/api' : '')

type SseMessage = { event: string; id: string; data: string }

/** 按 SSE 帧解析 UTF-8 数据，允许网络数据块切在任意位置。 */
async function readSse(response: Response, onMessage: (message: SseMessage) => void) {
  if (!response.body) throw new Error('服务器没有返回可读取的数据流')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let end = buffer.indexOf('\n\n')
      while (end !== -1) {
        const frame = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        const lines = frame.split('\n')
        onMessage({
          event: lines.find(line => line.startsWith('event:'))?.slice(6).trim() ?? '',
          id: lines.find(line => line.startsWith('id:'))?.slice(3).trim() ?? '',
          data: lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n'),
        })
        end = buffer.indexOf('\n\n')
      }
      if (done) break
    }
    if (buffer.trim()) throw new Error('数据流在事件中途断开')
  } catch (error) {
    await reader.cancel()
    throw error
  } finally {
    reader.releaseLock()
  }
}

/** 读取接口错误详情，保留 HTTP 状态作为兜底。 */
async function responseError(response: Response) {
  try { return (await response.json()).detail || `请求失败（${response.status}）` }
  catch { return `请求失败（${response.status}）` }
}

/** 所有 JSON 接口共用：失败时抛出服务端给出的中文原因，并保留状态码供调用方识别 409 等冲突。 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, init)
  if (!response.ok) throw Object.assign(new Error(await responseError(response)), { status: response.status })
  return response.json() as Promise<T>
}

const sessionPath = (sessionId: string) => `/v1/sessions/${encodeURIComponent(sessionId)}`
const jsonBody = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

/** 客户端已缓存的同一历史版本的长度；带上后服务端只返回新增的消息和事件。 */
export type SessionCursor = { historyVersion: number; messages: number; events: number }

export type McpServerConfig = {
  label?: string
  transport?: 'stdio' | 'sse' | string
  command?: string
  args?: string[]
  url?: string
  description?: string
  enabled?: boolean
  env?: Record<string, string>
  headers?: Record<string, string>
  include_tools?: string[]
  exclude_tools?: string[]
}

export type SystemConfigResponse = {
  has_api_key: boolean
  api_key_masked: string
  env_overrides_active: string[]
  llm: {
    provider: string
    model: string
    base_url: string
    api_base: string
    proxy_url: string
    max_tokens: number
    has_api_key: boolean
    api_key_masked: string
  }
  agent: {
    name: string
    max_iterations: number
    plan_enabled: boolean
    max_concurrent_runs: number
  }
  compress: {
    threshold_ratio: number
    recent_max_tokens: number | null
  }
  memory: {
    init_threshold: number
    update_interval: number
    min_turns: number
    idle_seconds: number
    cooldown_seconds: number
  }
  search: {
    has_tavily_api_key: boolean
    tavily_api_key_masked: string
  }
  heartbeat: {
    enabled: boolean
    interval: number
  }
  mcp: {
    enabled: boolean | null
    active: boolean
    servers: McpServerConfig[]
  }
}

export type SystemConfigPatch = {
  clear_api_key?: boolean
  clear_tavily_api_key?: boolean
  llm?: Partial<{
    provider: string
    api_key: string | null
    clear_api_key: boolean
    model: string
    base_url: string
    api_base: string
    proxy_url: string
    max_tokens: number
  }>
  agent?: Partial<{
    name: string
    max_iterations: number
    plan_enabled: boolean
    max_concurrent_runs: number
  }>
  compress?: Partial<{
    threshold_ratio: number
    recent_max_tokens: number | null
  }>
  memory?: Partial<{
    init_threshold: number
    update_interval: number
    min_turns: number
    idle_seconds: number
    cooldown_seconds: number
  }>
  search?: Partial<{
    tavily_api_key: string | null
    clear_tavily_api_key: boolean
  }>
  heartbeat?: Partial<{
    enabled: boolean
    interval: number
  }>
  mcp?: Partial<{
    enabled: boolean | null
    servers: McpServerConfig[]
  }>
}

export type McpToolBrief = { name: string; description: string }

export type McpServerStatus = {
  label: string
  transport: string
  status: 'connected' | 'failed' | 'disabled' | 'not_connected'
  error: string | null
  description?: string
  tool_count: number
  tools: McpToolBrief[]
}

export type McpStatusResponse = {
  enabled: boolean
  configured_enabled: boolean | null
  status: 'connected' | 'not_connected' | 'failed' | 'disabled'
  error: string | null
  total_servers: number
  connected_servers: number
  total_tools: number
  servers: McpServerStatus[]
}

export type WorkspaceInfo = {
  agent_id: string
  is_active: boolean
  path: string
  session_count: number
  wiki_page_count: number
  skill_count: number
  sandbox_enabled: boolean
  has_active_runs?: boolean
}

export type WorkspaceListResponse = {
  active_workspace: string
  workspaces: WorkspaceInfo[]
}

export type SkillInfo = {
  name: string
  description: string
  version: string
}

export function createApiClient() {
  let workspaceId = ''

  /** 当显式指定了工作空间时，自动在请求头携带 X-QRClaw-Workspace；未指定时保持原始参数不变。 */
  function withWorkspace(init?: RequestInit): RequestInit | undefined {
    if (!workspaceId) return init
    const headers = { ...(init?.headers as Record<string, string> | undefined), 'X-QRClaw-Workspace': workspaceId }
    return { ...init, headers }
  }

  const scopedRequest = <T>(path: string, init?: RequestInit) => request<T>(path, withWorkspace(init))
  const scopedWikiRequest = <T>(path: string, init?: RequestInit) => scopedRequest<T>(`/v1/wiki${path}`, init)

  return {
    /** 分页读取记忆摘要，网络取消不重复提交写入。 */
    listWikiPages(query: string, offset = 0, signal?: AbortSignal) {
      return scopedWikiRequest<WikiPageList>(`/pages?q=${encodeURIComponent(query)}&offset=${offset}&limit=20`, { signal })
    },
    /** 读取指定页面及其来源，切换选择时允许取消旧请求。 */
    getWikiPage(name: string, signal?: AbortSignal) {
      return scopedWikiRequest<WikiDetail>(`/pages/${encodeURIComponent(name)}`, { signal })
    },
    /** 使用用户打开草稿时的版本保存，只发送持久化字段。 */
    editWikiPage(name: string, draft: WikiDraft) {
      const { content, description, tags, related, revision } = draft
      return scopedWikiRequest<WikiDetail>(`/pages/${encodeURIComponent(name)}`, jsonBody('PATCH', { content, description, tags, related, revision }))
    },
    /** 按版本删除页面，后台已更新时返回冲突。 */
    deleteWikiPage(name: string, revision: number) {
      return scopedWikiRequest<{ deleted: boolean }>(`/pages/${encodeURIComponent(name)}?revision=${revision}`, { method: 'DELETE' })
    },
    /** 查询提取与整理进度。 */
    getWikiStatus(signal?: AbortSignal) { return scopedWikiRequest<WikiStatus>('/status', { signal }) },
    /** 执行不调用模型的规则检查。 */
    lintWiki() { return scopedWikiRequest<WikiLintReport>('/lint', { method: 'POST' }) },
    /** 排队重新整理，断线不会重发。 */
    repairWikiPage(name: string) {
      return scopedWikiRequest<WikiDetail>(`/pages/${encodeURIComponent(name)}/repair`, { method: 'POST' })
    },
    health() { return scopedRequest<{ status: string }>('/health') },
    listSessions() { return scopedRequest<SessionSummary[]>('/v1/sessions') },
    createSession(sessionId?: string) {
      return scopedRequest<SessionDetail>('/v1/sessions', jsonBody('POST', sessionId ? { session_id: sessionId } : {}))
    },
    /** 读取会话；传入已缓存的游标时只取新增部分，历史版本不同由服务端返回完整内容。 */
    getSession(sessionId: string, cursor?: SessionCursor) {
      const query = cursor ? `?history_version=${cursor.historyVersion}&messages_from=${cursor.messages}&events_from=${cursor.events}` : ''
      return scopedRequest<SessionDetail>(`${sessionPath(sessionId)}${query}`)
    },
    async deleteSession(sessionId: string) {
      await scopedRequest<{ deleted: boolean }>(sessionPath(sessionId), { method: 'DELETE' })
    },
    /** 保存自定义会话标题。 */
    renameSession(sessionId: string, title: string) {
      return scopedRequest<SessionDetail>(sessionPath(sessionId), jsonBody('PATCH', { title }))
    },
    /** 删除最后一条用户消息及其所属轮次。 */
    deleteLatestUserMessage(sessionId: string, messageId: string) {
      return scopedRequest<SessionDetail>(`${sessionPath(sessionId)}/messages/${encodeURIComponent(messageId)}`, { method: 'DELETE' })
    },
    /** 提交一次流式聊天；连接中断只报错，不重发可能已经执行的任务。 */
    async chatStream(sessionId: string, content: string, onDelta: (delta: string) => void, onReady?: () => void, editMessageId?: string, planEnabled = false) {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (workspaceId) headers['X-QRClaw-Workspace'] = workspaceId
      const response = await fetch(`${baseUrl}/v1/chat/completions`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ model: 'qrclaw-agent', session_id: sessionId,
          messages: [{ role: 'user', content }], stream: true, plan_enabled: planEnabled,
          ...(editMessageId ? { edit_message_id: editMessageId } : {}) }),
      })
      if (!response.ok) throw new Error(await responseError(response))
      onReady?.()
      let finished = false
      await readSse(response, message => {
        if (message.data === '[DONE]') { finished = true; return }
        if (!message.data) return
        const payload = JSON.parse(message.data)
        if (payload.error) throw new Error(payload.error.message || '任务执行失败')
        const delta = payload.choices?.[0]?.delta?.content
        if (typeof delta === 'string' && delta) onDelta(delta)
      })
      if (!finished) throw new Error('聊天连接提前断开，请查看会话执行状态')
    },
    /** 从指定事件序号之后续接会话过程流。 */
    async streamEvents(sessionId: string, after: number, onEvent: (index: number, event: SessionEvent) => void,
      signal?: AbortSignal, onLiveText?: (text: string) => void) {
      const response = await fetch(`${baseUrl}${sessionPath(sessionId)}/events/stream?after=${after}`, withWorkspace({ signal }))
      if (!response.ok) throw new Error(await responseError(response))
      await readSse(response, message => {
        if (message.event === 'progress' && message.id) onEvent(Number(message.id), JSON.parse(message.data))
        if (message.event === 'live_text' && message.data) onLiveText?.(JSON.parse(message.data).text ?? '')
      })
    },
    getWorkspace() { return workspaceId || 'default' },
    setWorkspace(agentId: string) { workspaceId = agentId.trim() },
    getSystemConfig(signal?: AbortSignal) {
      return scopedRequest<SystemConfigResponse>('/v1/system/config', { signal })
    },
    updateSystemConfig(patch: SystemConfigPatch) {
      return scopedRequest<SystemConfigResponse>('/v1/system/config', jsonBody('PATCH', patch))
    },
    getMcpStatus(signal?: AbortSignal) {
      return scopedRequest<McpStatusResponse>('/v1/system/mcp', { signal })
    },
    reconnectMcp() {
      return scopedRequest<McpStatusResponse>('/v1/system/mcp/reconnect', { method: 'POST' })
    },
    listWorkspaces(signal?: AbortSignal) {
      return scopedRequest<WorkspaceListResponse>('/v1/system/workspaces', { signal })
    },
    async createWorkspace(agentId: string, switchImmediate = false) {
      const created = await scopedRequest<WorkspaceInfo & { active_workspace: string }>(
        '/v1/system/workspaces',
        jsonBody('POST', { agent_id: agentId, switch: switchImmediate }),
      )
      if (switchImmediate && created.active_workspace) workspaceId = created.active_workspace
      return created
    },
    async switchWorkspace(agentId: string) {
      const switched = await scopedRequest<WorkspaceInfo & { active_workspace: string }>(
        '/v1/system/workspaces/switch',
        jsonBody('POST', { agent_id: agentId }),
      )
      workspaceId = switched.active_workspace || agentId.trim()
      return switched
    },
    deleteWorkspace(agentId: string) {
      return scopedRequest<{ deleted: boolean; agent_id: string; active_workspace: string }>(
        `/v1/system/workspaces/${encodeURIComponent(agentId)}`,
        { method: 'DELETE' },
      )
    },
    listSkills(signal?: AbortSignal) {
      return scopedRequest<SkillInfo[]>('/v1/system/skills', { signal })
    },
  }
}
