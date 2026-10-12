import type { SessionDetail, SessionSummary } from './types'

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

export function createApiClient() {
  return {
    async health() {
      const response = await fetch(`${baseUrl}/health`)
      if (!response.ok) throw new Error('health check failed')
      return response.json()
    },
    async listSessions() {
      const response = await fetch(`${baseUrl}/v1/sessions`)
      if (!response.ok) throw new Error('list sessions failed')
      return (await response.json()) as SessionSummary[]
    },
    async createSession(sessionId?: string) {
      const response = await fetch(`${baseUrl}/v1/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
      })
      if (!response.ok) throw new Error('create session failed')
      return (await response.json()) as SessionDetail
    },
    async getSession(sessionId: string) {
      const response = await fetch(`${baseUrl}/v1/sessions/${encodeURIComponent(sessionId)}`)
      if (!response.ok) throw new Error('get session failed')
      return (await response.json()) as SessionDetail
    },
    async deleteSession(sessionId: string) {
      const response = await fetch(`${baseUrl}/v1/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'DELETE',
      })
      if (!response.ok) throw new Error('delete session failed')
    },
    /** 保存自定义会话标题。 */
    async renameSession(sessionId: string, title: string) {
      const response = await fetch(`${baseUrl}/v1/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }),
      })
      if (!response.ok) throw new Error(await responseError(response))
      return (await response.json()) as SessionDetail
    },
    /** 删除最后一条用户消息及其所属轮次。 */
    async deleteLatestUserMessage(sessionId: string, messageId: string) {
      const response = await fetch(`${baseUrl}/v1/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`, {
        method: 'DELETE',
      })
      if (!response.ok) throw new Error(await responseError(response))
      return (await response.json()) as SessionDetail
    },
    async chat(sessionId: string, content: string, planEnabled = false) {
      const response = await fetch(`${baseUrl}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'qrclaw-agent',
          session_id: sessionId,
          messages: [{ role: 'user', content }],
          plan_enabled: planEnabled,
        }),
      })

      const body = await response.json()
      if (!response.ok) {
        throw new Error(body.detail || 'chat request failed')
      }
      return body.choices?.[0]?.message?.content ?? ''
    },
    /** 提交一次流式聊天；连接中断只报错，不重发可能已经执行的任务。 */
    async chatStream(sessionId: string, content: string, onDelta: (delta: string) => void, onReady?: () => void, editMessageId?: string, planEnabled = false) {
      const response = await fetch(`${baseUrl}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
    async streamEvents(sessionId: string, after: number, onEvent: (index: number, event: NonNullable<SessionDetail['events']>[number]) => void,
      signal?: AbortSignal, onLiveText?: (text: string) => void) {
      const response = await fetch(`${baseUrl}/v1/sessions/${encodeURIComponent(sessionId)}/events/stream?after=${after}`, { signal })
      if (!response.ok) throw new Error(await responseError(response))
      await readSse(response, message => {
        if (message.event === 'progress' && message.id) onEvent(Number(message.id), JSON.parse(message.data))
        if (message.event === 'live_text' && message.data) onLiveText?.(JSON.parse(message.data).text ?? '')
      })
    },
  }
}
