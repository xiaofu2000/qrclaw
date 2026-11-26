import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  createApiClient,
  McpServerConfig,
  McpStatusResponse,
  SkillInfo,
  SystemConfigPatch,
  SystemConfigResponse,
  WorkspaceInfo,
} from '../../lib/api'

type SettingsApi = Pick<
  ReturnType<typeof createApiClient>,
  | 'getSystemConfig'
  | 'updateSystemConfig'
  | 'getMcpStatus'
  | 'reconnectMcp'
  | 'listWorkspaces'
  | 'createWorkspace'
  | 'switchWorkspace'
  | 'deleteWorkspace'
  | 'listSkills'
  | 'setWorkspace'
>

export type ConfigFormState = {
  model: string
  baseUrl: string
  proxyUrl: string
  apiKeyInput: string
  clearApiKey: boolean
  maxTokens: string
  agentName: string
  maxIterations: string
  planEnabled: boolean
  maxConcurrentRuns: string
  compressThresholdRatio: string
  recentMaxTokens: string
  memoryInitThreshold: string
  memoryUpdateInterval: string
  memoryMinTurns: string
  memoryIdleSeconds: string
  memoryCooldownSeconds: string
  tavilyApiKeyInput: string
  clearTavilyApiKey: boolean
}

export type McpServerDraft = {
  id: string
  label: string
  transport: 'stdio' | 'sse'
  command: string
  argsText: string
  url: string
  description: string
  enabled: boolean
  extra: Record<string, unknown>
}

const DEFAULT_FORM: ConfigFormState = {
  model: 'gpt-4o',
  baseUrl: '',
  proxyUrl: '',
  apiKeyInput: '',
  clearApiKey: false,
  maxTokens: '485000',
  agentName: 'QRClaw',
  maxIterations: '100',
  planEnabled: false,
  maxConcurrentRuns: '4',
  compressThresholdRatio: '0.9',
  recentMaxTokens: '',
  memoryInitThreshold: '5000',
  memoryUpdateInterval: '5000',
  memoryMinTurns: '3',
  memoryIdleSeconds: '120',
  memoryCooldownSeconds: '300',
  tavilyApiKeyInput: '',
  clearTavilyApiKey: false,
}

function toConfigForm(cfg: SystemConfigResponse): ConfigFormState {
  return {
    model: cfg.llm.model,
    baseUrl: cfg.llm.base_url || cfg.llm.api_base || '',
    proxyUrl: cfg.llm.proxy_url || '',
    apiKeyInput: '',
    clearApiKey: false,
    maxTokens: String(cfg.llm.max_tokens),
    agentName: cfg.agent.name,
    maxIterations: String(cfg.agent.max_iterations),
    planEnabled: cfg.agent.plan_enabled,
    maxConcurrentRuns: String(cfg.agent.max_concurrent_runs),
    compressThresholdRatio: String(cfg.compress.threshold_ratio),
    recentMaxTokens: cfg.compress.recent_max_tokens !== null ? String(cfg.compress.recent_max_tokens) : '',
    memoryInitThreshold: String(cfg.memory.init_threshold),
    memoryUpdateInterval: String(cfg.memory.update_interval),
    memoryMinTurns: String(cfg.memory.min_turns),
    memoryIdleSeconds: String(cfg.memory.idle_seconds),
    memoryCooldownSeconds: String(cfg.memory.cooldown_seconds),
    tavilyApiKeyInput: '',
    clearTavilyApiKey: false,
  }
}

function toMcpDrafts(servers: McpServerConfig[]): McpServerDraft[] {
  return servers.map((srv, index) => {
    const { label, transport, command, args, url, description, enabled, ...rest } = srv
    return {
      id: `mcp-${index}-${label || index}`,
      label: label || `server-${index}`,
      transport: transport === 'sse' ? 'sse' : 'stdio',
      command: command || '',
      argsText: Array.isArray(args) ? args.join(' ') : '',
      url: url || '',
      description: description || '',
      enabled: enabled !== false,
      extra: rest,
    }
  })
}

function parseArgs(argsText: string): string[] {
  const trimmed = argsText.trim()
  if (!trimmed) return []
  return trimmed.split(/\s+/).filter(Boolean)
}

export type SettingsWorkspaceOptions = {
  onWorkspaceSynced?: (agentId: string) => void
  onWorkspaceSwitched?: (agentId: string) => void
}

/** 系统设置与多工作空间管理 ViewModel：统一维护表单草稿、热更新、MCP 重连与工作空间隔离切换。 */
export function useSettingsWorkspace(api: SettingsApi, visible: boolean, options?: SettingsWorkspaceOptions) {
  const [config, setConfig] = useState<SystemConfigResponse | null>(null)
  const [form, setForm] = useState<ConfigFormState>(DEFAULT_FORM)
  const [dirty, setDirty] = useState(false)
  const [mcpStatus, setMcpStatus] = useState<McpStatusResponse | null>(null)
  const [mcpEnabledMode, setMcpEnabledModeState] = useState<'auto' | 'enabled' | 'disabled'>('auto')
  const [mcpServers, setMcpServers] = useState<McpServerDraft[]>([])
  const [mcpDirty, setMcpDirty] = useState(false)
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([])
  const [activeWorkspace, setActiveWorkspace] = useState('default')
  const [newWorkspaceId, setNewWorkspaceId] = useState('')
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [savingConfig, setSavingConfig] = useState(false)
  const [reconnectingMcp, setReconnectingMcp] = useState(false)
  const [mutatingWorkspace, setMutatingWorkspace] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)

  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty
  const mcpDirtyRef = useRef(mcpDirty)
  mcpDirtyRef.current = mcpDirty
  const optionsRef = useRef(options)
  optionsRef.current = options
  const epochRef = useRef(0)
  const nextDraftSeqRef = useRef(1)

  const refresh = useCallback(() => {
    setError('')
    setRefreshKey(value => value + 1)
  }, [])

  useEffect(() => {
    if (!visible) return
    let disposed = false
    const controller = new AbortController()
    const currentEpoch = ++epochRef.current
    setLoading(true)

    async function loadAll() {
      const results = await Promise.allSettled([
        api.getSystemConfig(controller.signal),
        api.listWorkspaces(controller.signal),
        api.getMcpStatus(controller.signal),
        api.listSkills(controller.signal),
      ])
      if (disposed || currentEpoch !== epochRef.current) return
      const [cfgRes, wsRes, mcpRes, skillRes] = results

      if (cfgRes.status === 'fulfilled') {
        const cfg = cfgRes.value
        setConfig(cfg)
        if (!dirtyRef.current) setForm(toConfigForm(cfg))
        if (!mcpDirtyRef.current) {
          setMcpEnabledModeState(cfg.mcp.enabled === null ? 'auto' : cfg.mcp.enabled ? 'enabled' : 'disabled')
          setMcpServers(toMcpDrafts(cfg.mcp.servers || []))
        }
      }
      if (wsRes.status === 'fulfilled') {
        setWorkspaces(wsRes.value.workspaces)
        setActiveWorkspace(wsRes.value.active_workspace)
        api.setWorkspace?.(wsRes.value.active_workspace)
        optionsRef.current?.onWorkspaceSynced?.(wsRes.value.active_workspace)
      }
      if (mcpRes.status === 'fulfilled') setMcpStatus(mcpRes.value)
      if (skillRes.status === 'fulfilled') setSkills(skillRes.value)

      const rejected = results.find(item => item.status === 'rejected')
      if (rejected?.status === 'rejected' && rejected.reason?.name !== 'AbortError') {
        setError(String(rejected.reason?.message ?? rejected.reason))
      }
      setLoading(false)
    }

    void loadAll()
    return () => {
      disposed = true
      controller.abort()
    }
  }, [api, visible, refreshKey])

  function updateForm(patch: Partial<ConfigFormState>) {
    setForm(current => ({ ...current, ...patch }))
    setDirty(true)
    setNotice('')
  }

  function resetForm() {
    if (config) setForm(toConfigForm(config))
    setDirty(false)
    setError('')
    setNotice('')
  }

  /** 提交系统配置更新；未输入新密钥时不发送 api_key 字段，确保原密钥不被覆盖。 */
  async function saveConfig() {
    if (savingConfig) return
    setSavingConfig(true)
    setError('')
    setNotice('')
    try {
      const llmPatch: NonNullable<SystemConfigPatch['llm']> = {
        model: form.model.trim(),
        base_url: form.baseUrl.trim(),
        proxy_url: form.proxyUrl.trim(),
        max_tokens: Number(form.maxTokens),
      }
      const patch: SystemConfigPatch = {
        llm: llmPatch,
        agent: {
          name: form.agentName.trim(),
          max_iterations: Number(form.maxIterations),
          plan_enabled: form.planEnabled,
          max_concurrent_runs: Number(form.maxConcurrentRuns),
        },
        compress: {
          threshold_ratio: Number(form.compressThresholdRatio),
          recent_max_tokens: form.recentMaxTokens.trim() ? Number(form.recentMaxTokens) : null,
        },
        memory: {
          init_threshold: Number(form.memoryInitThreshold),
          update_interval: Number(form.memoryUpdateInterval),
          min_turns: Number(form.memoryMinTurns),
          idle_seconds: Number(form.memoryIdleSeconds),
          cooldown_seconds: Number(form.memoryCooldownSeconds),
        },
      }
      if (form.clearApiKey) {
        patch.clear_api_key = true
        llmPatch.api_key = ''
      } else if (form.apiKeyInput.trim()) {
        llmPatch.api_key = form.apiKeyInput.trim()
      }

      if (form.clearTavilyApiKey) {
        patch.clear_tavily_api_key = true
        patch.search = { tavily_api_key: '' }
      } else if (form.tavilyApiKeyInput.trim()) {
        patch.search = { tavily_api_key: form.tavilyApiKeyInput.trim() }
      }

      const updated = await api.updateSystemConfig(patch)
      setConfig(updated)
      setForm(toConfigForm(updated))
      setDirty(false)
      setNotice('配置已保存并立即生效')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setSavingConfig(false)
    }
  }

  function setMcpEnabledMode(mode: 'auto' | 'enabled' | 'disabled') {
    setMcpEnabledModeState(mode)
    setMcpDirty(true)
    setNotice('')
  }

  function addMcpServer() {
    const seq = nextDraftSeqRef.current++
    setMcpServers(current => [
      ...current,
      {
        id: `new-mcp-${seq}`,
        label: `server-${current.length}`,
        transport: 'stdio',
        command: '',
        argsText: '',
        url: '',
        description: '',
        enabled: true,
        extra: {},
      },
    ])
    setMcpDirty(true)
    setNotice('')
  }

  function updateMcpServer(id: string, patch: Partial<Omit<McpServerDraft, 'id' | 'extra'>>) {
    setMcpServers(current => current.map(item => (item.id === id ? { ...item, ...patch } : item)))
    setMcpDirty(true)
    setNotice('')
  }

  function removeMcpServer(id: string) {
    setMcpServers(current => current.filter(item => item.id !== id))
    setMcpDirty(true)
    setNotice('')
  }

  /** 保存 MCP 服务列表配置并立即重连生效。 */
  async function saveAndReconnectMcp() {
    if (reconnectingMcp || savingConfig) return
    setReconnectingMcp(true)
    setError('')
    setNotice('')
    try {
      const serversPayload: McpServerConfig[] = mcpServers.map(item => {
        const base: McpServerConfig = {
          ...(item.extra as McpServerConfig),
          label: item.label.trim(),
          transport: item.transport,
        }
        if (item.description.trim()) base.description = item.description.trim()
        if (!item.enabled) base.enabled = false
        if (item.transport === 'stdio') {
          base.command = item.command.trim()
          base.args = parseArgs(item.argsText)
          delete base.url
        } else {
          base.url = item.url.trim()
          delete base.command
          delete base.args
        }
        return base
      })

      const enabledVal = mcpEnabledMode === 'auto' ? null : mcpEnabledMode === 'enabled'
      const updatedCfg = await api.updateSystemConfig({ mcp: { enabled: enabledVal, servers: serversPayload } })
      setConfig(updatedCfg)
      setMcpServers(toMcpDrafts(updatedCfg.mcp.servers || []))
      setMcpDirty(false)
      const latestMcp = await api.reconnectMcp()
      setMcpStatus(latestMcp)
      setNotice('MCP 配置已保存并完成重连')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setReconnectingMcp(false)
    }
  }

  /** 不修改配置文件，仅按最新配置重新建立常驻 MCP 连接。 */
  async function reconnectMcp() {
    if (reconnectingMcp) return
    setReconnectingMcp(true)
    setError('')
    setNotice('')
    try {
      const latest = await api.reconnectMcp()
      setMcpStatus(latest)
      setNotice(latest.status === 'connected' ? `MCP 已重连（${latest.total_tools} 个可用工具）` : 'MCP 重连已执行，请检查服务状态')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setReconnectingMcp(false)
    }
  }

  /** 新建独立工作空间（可选创建后立即切换）。 */
  async function createWorkspace(customId?: string, switchImmediate = true) {
    const targetId = (customId ?? newWorkspaceId).trim()
    if (!targetId || mutatingWorkspace) return
    setMutatingWorkspace(true)
    setError('')
    setNotice('')
    try {
      const created = await api.createWorkspace(targetId, switchImmediate)
      setNewWorkspaceId('')
      const listed = await api.listWorkspaces()
      setWorkspaces(listed.workspaces)
      setActiveWorkspace(listed.active_workspace)
      if (switchImmediate) {
        const targetActive = created.active_workspace || targetId
        api.setWorkspace?.(targetActive)
        const nextSkills = await api.listSkills()
        setSkills(nextSkills)
        optionsRef.current?.onWorkspaceSwitched?.(targetActive)
        setNotice(`已创建并切换到工作空间「${targetActive}」`)
      } else {
        setNotice(`已创建工作空间「${targetId}」`)
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setMutatingWorkspace(false)
    }
  }

  /** 切换当前激活工作空间，并联动主应用重载会话与记忆库。 */
  async function switchWorkspace(agentId: string) {
    const cleaned = agentId.trim()
    if (!cleaned || cleaned === activeWorkspace || mutatingWorkspace) return
    setMutatingWorkspace(true)
    setError('')
    setNotice('')
    try {
      const switched = await api.switchWorkspace(cleaned)
      const nextActive = switched.active_workspace || cleaned
      api.setWorkspace?.(nextActive)
      setActiveWorkspace(nextActive)
      const [listed, nextSkills] = await Promise.all([api.listWorkspaces(), api.listSkills()])
      setWorkspaces(listed.workspaces)
      setSkills(nextSkills)
      optionsRef.current?.onWorkspaceSwitched?.(nextActive)
      setNotice(`已切换到工作空间「${nextActive}」，会话与记忆库已隔离切换`)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setMutatingWorkspace(false)
    }
  }

  /** 删除闲置的非默认工作空间。 */
  async function deleteWorkspace(agentId: string) {
    if (!agentId || mutatingWorkspace) return
    setMutatingWorkspace(true)
    setError('')
    setNotice('')
    try {
      await api.deleteWorkspace(agentId)
      const listed = await api.listWorkspaces()
      setWorkspaces(listed.workspaces)
      setActiveWorkspace(listed.active_workspace)
      setNotice(`工作空间「${agentId}」已删除`)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setMutatingWorkspace(false)
    }
  }

  return {
    config,
    form,
    dirty,
    updateForm,
    resetForm,
    saveConfig,
    mcpStatus,
    mcpEnabledMode,
    setMcpEnabledMode,
    mcpServers,
    mcpDirty,
    addMcpServer,
    updateMcpServer,
    removeMcpServer,
    saveAndReconnectMcp,
    reconnectMcp,
    workspaces,
    activeWorkspace,
    newWorkspaceId,
    setNewWorkspaceId,
    createWorkspace,
    switchWorkspace,
    deleteWorkspace,
    skills,
    loading,
    savingConfig,
    reconnectingMcp,
    mutatingWorkspace,
    error,
    notice,
    refresh,
  }
}
