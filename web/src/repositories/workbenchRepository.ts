import { mapConversation, mapMessage, mapSettings } from '../models/mappers'
import type {
  Conversation,
  DirectoryListing,
  Message,
  Settings,
  SettingsInput,
} from '../models/workbench'
import { ApiService } from '../services/apiService'

const ACTIVE_CONVERSATION_KEY = 'qrclaw.activeConversation'
const ACCESS_TOKEN_KEY = 'qrclaw.localAccessToken'

/** 负责会话、设置与轻量客户端恢复信息。 */
export class WorkbenchRepository {
  private readonly api: ApiService

  constructor(api: ApiService) {
    this.api = api
  }

  async listConversations(): Promise<Conversation[]> {
    return (await this.api.listConversations()).map(mapConversation)
  }

  async createConversation(title: string, workspacePath: string): Promise<Conversation> {
    return mapConversation(await this.api.createConversation(title, workspacePath))
  }

  async browseDirectories(path = ''): Promise<DirectoryListing> {
    const listing = await this.api.browseDirectories(path)
    return {
      currentPath: listing.current_path,
      parentPath: listing.parent_path,
      homePath: listing.home_path,
      directories: listing.directories,
    }
  }

  async renameConversation(id: string, title: string): Promise<Conversation> {
    return mapConversation(await this.api.updateConversation(id, title))
  }

  async deleteConversation(id: string): Promise<void> {
    await this.api.deleteConversation(id)
    localStorage.removeItem(this.runKey(id))
  }

  /** 聊天历史只展示用户输入和最终回复，工具交互由执行时间线展示。 */
  async getMessages(id: string): Promise<Message[]> {
    return (await this.api.getMessages(id))
      .filter((message) => message.role === 'user'
        || (message.role === 'assistant' && !message.tool_calls?.length))
      .map(mapMessage)
  }

  async getSettings(): Promise<Settings> {
    return mapSettings(await this.api.getSettings())
  }

  async updateSettings(input: SettingsInput): Promise<Settings> {
    return mapSettings(await this.api.updateSettings(input))
  }

  async testConnection(): Promise<string> {
    const result = await this.api.testConnection()
    return result.response
  }

  getActiveConversationId(): string | null {
    return localStorage.getItem(ACTIVE_CONVERSATION_KEY)
  }

  setActiveConversationId(id: string): void {
    localStorage.setItem(ACTIVE_CONVERSATION_KEY, id)
  }

  getRunId(conversationId: string): string | null {
    return localStorage.getItem(this.runKey(conversationId))
  }

  setRunId(conversationId: string, runId: string): void {
    localStorage.setItem(this.runKey(conversationId), runId)
  }

  private runKey(conversationId: string): string {
    return `qrclaw.activeRun.${conversationId}`
  }
}

/** 读取本地访问令牌，构建环境变量优先。 */
export function readAccessToken(): string {
  return import.meta.env.VITE_QRCLAW_ACCESS_TOKEN || localStorage.getItem(ACCESS_TOKEN_KEY) || ''
}

/** 保存本地服务访问令牌，不保存模型 API Key。 */
export function saveAccessToken(token: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, token.trim())
}
