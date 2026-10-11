import type { Message, SessionDetail, SessionSummary } from './types'

const baseUrl = import.meta.env.VITE_QRCLAW_API_BASE_URL ?? '/api'

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
    async chat(sessionId: string, messages: Message[]) {
      const response = await fetch(`${baseUrl}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'qrclaw-agent',
          session_id: sessionId,
          messages: messages.map((message) => ({
            role: message.role,
            content: message.content,
          })),
        }),
      })

      const body = await response.json()
      if (!response.ok) {
        throw new Error(body.detail || 'chat request failed')
      }
      return body.choices?.[0]?.message?.content ?? ''
    },
  }
}
