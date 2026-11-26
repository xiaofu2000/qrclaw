import { useState } from 'react'
import {
  AlertTriangle,
  Blocks,
  CheckCircle2,
  ChevronDown,
  Cpu,
  FolderGit2,
  KeyRound,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Shield,
  Sparkles,
  Trash2,
} from 'lucide-react'
import type { createApiClient } from '../../lib/api'
import { useSettingsWorkspace } from './useSettingsWorkspace'
import './settings.css'

const MCP_STATUS_LABEL: Record<string, string> = {
  connected: '已连接',
  not_connected: '待首次运行连接',
  failed: '连接失败',
  disabled: '未启用',
}

export type SettingsViewProps = {
  api: ReturnType<typeof createApiClient>
  visible?: boolean
  onWorkspaceSynced?: (agentId: string) => void
  onWorkspaceSwitched?: (agentId: string) => void
}

/** 系统设置入口组件：包含工作空间隔离、模型与运行参数热更新、MCP 服务状态与已安装技能四个区块。 */
export function SettingsView({ api, visible = true, onWorkspaceSynced, onWorkspaceSwitched }: SettingsViewProps) {
  const model = useSettingsWorkspace(api, visible, { onWorkspaceSynced, onWorkspaceSwitched })
  return (
    <div hidden={!visible} className="settings-workspace" role="main" aria-label="系统设置与工作空间管理">
      <SettingsPanel model={model} />
    </div>
  )
}

export function SettingsPanel({ model }: { model: ReturnType<typeof useSettingsWorkspace> }) {
  const [confirmDeleteWs, setConfirmDeleteWs] = useState('')
  const envOverrides = model.config?.env_overrides_active ?? []

  return (
    <>
      <header className="settings-header">
        <div>
          <h1>系统设置与工作空间</h1>
          <p>管理项目隔离工作空间、模型与运行参数热更新、MCP 工具连接与已安装技能</p>
        </div>
        <div className="settings-actions">
          <button type="button" onClick={model.refresh} disabled={model.loading}>
            <RefreshCw size={15} aria-hidden="true" className={model.loading ? 'spin' : undefined} />刷新状态
          </button>
        </div>
      </header>

      <div className="settings-body">
        {model.error && (
          <div className="settings-banner settings-error" role="alert">
            <span>{model.error}</span>
            <button type="button" onClick={model.refresh}>重新载入</button>
          </div>
        )}
        {model.notice && (
          <div className="settings-banner settings-notice" role="status">
            <CheckCircle2 size={16} aria-hidden="true" />
            <span>{model.notice}</span>
          </div>
        )}
        {envOverrides.length > 0 && (
          <div className="settings-banner settings-warning" role="note">
            <AlertTriangle size={16} aria-hidden="true" />
            <span>
              当前以下字段正受环境变量覆盖（修改配置文件后仍以环境变量为准）：
              <code>{envOverrides.join('、')}</code>
            </span>
          </div>
        )}

        {/* 1. 工作空间（项目隔离）管理区 */}
        <section className="settings-card" id="settings-workspaces" aria-labelledby="heading-workspaces">
          <div className="settings-card-header">
            <div className="settings-card-title">
              <FolderGit2 size={19} aria-hidden="true" />
              <div>
                <h2 id="heading-workspaces">工作空间（项目隔离）</h2>
                <p>不同项目的会话历史、Wiki 长期记忆与技能目录物理隔离，切换工作空间不会中断其他空间的后台任务。</p>
              </div>
            </div>
            <span className="settings-badge active">当前：{model.activeWorkspace}</span>
          </div>

          <div className="workspace-grid">
            {model.workspaces.map(ws => (
              <div key={ws.agent_id} className={`workspace-card ${ws.is_active ? 'is-active' : ''}`}>
                <div className="workspace-card-top">
                  <strong>{ws.agent_id}</strong>
                  <div className="workspace-badges">
                    {ws.is_active && <span className="settings-badge active">当前使用</span>}
                    {ws.sandbox_enabled && (
                      <span className="settings-badge neutral" title="已启用 Docker 沙箱隔离">
                        <Shield size={12} aria-hidden="true" />沙箱隔离
                      </span>
                    )}
                    {ws.has_active_runs && <span className="settings-badge warning">任务运行中</span>}
                  </div>
                </div>
                <div className="workspace-metrics">
                  <span>会话 <b>{ws.session_count}</b></span>
                  <span>记忆页 <b>{ws.wiki_page_count}</b></span>
                  <span>技能 <b>{ws.skill_count}</b></span>
                </div>
                <code className="workspace-path" title={ws.path}>{ws.path}</code>
                <div className="workspace-card-actions">
                  {ws.is_active ? (
                    <button type="button" disabled className="settings-btn-secondary">已激活</button>
                  ) : (
                    <button
                      type="button"
                      className="settings-btn-primary"
                      disabled={model.mutatingWorkspace}
                      onClick={() => void model.switchWorkspace(ws.agent_id)}
                    >
                      切换到此空间
                    </button>
                  )}
                  {ws.agent_id !== 'default' && !ws.is_active && (
                    confirmDeleteWs === ws.agent_id ? (
                      <div className="workspace-delete-confirm">
                        <button
                          type="button"
                          className="settings-btn-danger"
                          disabled={model.mutatingWorkspace}
                          onClick={() => { setConfirmDeleteWs(''); void model.deleteWorkspace(ws.agent_id) }}
                        >
                          确认删除
                        </button>
                        <button type="button" onClick={() => setConfirmDeleteWs('')}>取消</button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="settings-btn-danger-ghost"
                        aria-label={`删除工作空间 ${ws.agent_id}`}
                        disabled={model.mutatingWorkspace || Boolean(ws.has_active_runs)}
                        onClick={() => setConfirmDeleteWs(ws.agent_id)}
                      >
                        <Trash2 size={14} aria-hidden="true" />删除
                      </button>
                    )
                  )}
                </div>
              </div>
            ))}
          </div>

          <form
            className="workspace-create-bar"
            onSubmit={event => { event.preventDefault(); void model.createWorkspace(undefined, true) }}
          >
            <input
              aria-label="新工作空间 ID"
              value={model.newWorkspaceId}
              onChange={event => model.setNewWorkspaceId(event.target.value)}
              placeholder="输入新工作空间 ID（如 rea、qrclaw、frontend，仅限字母/数字/_/-）"
            />
            <button
              type="submit"
              className="settings-btn-primary"
              disabled={model.mutatingWorkspace || !model.newWorkspaceId.trim()}
            >
              <Plus size={15} aria-hidden="true" />新建并切换
            </button>
            <button
              type="button"
              disabled={model.mutatingWorkspace || !model.newWorkspaceId.trim()}
              onClick={() => void model.createWorkspace(undefined, false)}
            >
              仅新建
            </button>
          </form>
        </section>

        {/* 2. 模型与运行参数设置区 */}
        <section className="settings-card" id="settings-llm" aria-labelledby="heading-llm">
          <div className="settings-card-header">
            <div className="settings-card-title">
              <Cpu size={19} aria-hidden="true" />
              <div>
                <h2 id="heading-llm">模型与运行参数</h2>
                <p>通过 LiteLLM 统一接入 OpenAI / Anthropic / Gemini / DeepSeek / MiniMax 等渠道，保存后立即对后续对话热生效。</p>
              </div>
            </div>
          </div>

          <form className="settings-form" onSubmit={event => { event.preventDefault(); void model.saveConfig() }}>
            <div className="settings-grid-two">
              <label>
                <span>模型名称（Model）</span>
                <input
                  value={model.form.model}
                  onChange={event => model.updateForm({ model: event.target.value })}
                  placeholder="如 openai/gpt-4o、deepseek/deepseek-chat、minimax/MiniMax-M2.7-highspeed"
                />
              </label>
              <label>
                <span>接口地址（Base URL，可选）</span>
                <input
                  value={model.form.baseUrl}
                  onChange={event => model.updateForm({ baseUrl: event.target.value })}
                  placeholder="默认按提供商官方地址，自定义代理或网关时填写"
                />
              </label>
              <label>
                <span>
                  API Key
                  <small className="field-badge">
                    <KeyRound size={12} aria-hidden="true" />
                    {model.config?.has_api_key ? `已配置（${model.config.api_key_masked}）` : '未配置'}
                  </small>
                </span>
                <input
                  type="password"
                  autoComplete="off"
                  value={model.form.apiKeyInput}
                  onChange={event => model.updateForm({ apiKeyInput: event.target.value, clearApiKey: false })}
                  placeholder={model.config?.has_api_key ? '留空保留原密钥，输入新值则替换' : '输入模型服务商 API Key'}
                />
              </label>
              <label>
                <span>最大上下文窗口（Max Tokens）</span>
                <input
                  type="number"
                  min={1024}
                  step={1000}
                  value={model.form.maxTokens}
                  onChange={event => model.updateForm({ maxTokens: event.target.value })}
                />
              </label>
              <label>
                <span>HTTP 代理地址（Proxy URL，可选）</span>
                <input
                  value={model.form.proxyUrl}
                  onChange={event => model.updateForm({ proxyUrl: event.target.value })}
                  placeholder="如 http://127.0.0.1:7890"
                />
              </label>
              <label>
                <span>
                  Tavily 搜索密钥（可选）
                  <small className="field-badge">
                    {model.config?.search.has_tavily_api_key
                      ? `已配置（${model.config.search.tavily_api_key_masked}）`
                      : '未配置（默认回退免费搜索）'}
                  </small>
                </span>
                <input
                  type="password"
                  autoComplete="off"
                  value={model.form.tavilyApiKeyInput}
                  onChange={event => model.updateForm({ tavilyApiKeyInput: event.target.value, clearTavilyApiKey: false })}
                  placeholder={model.config?.search.has_tavily_api_key ? '留空保留原密钥，输入新值则替换' : '输入 Tavily API Key'}
                />
              </label>
            </div>

            <div className="settings-subdivider">Agent 调度、上下文压缩与后台记忆提取</div>

            <div className="settings-grid-three">
              <label>
                <span>Agent 名称</span>
                <input
                  value={model.form.agentName}
                  onChange={event => model.updateForm({ agentName: event.target.value })}
                />
              </label>
              <label>
                <span>单轮最大工具迭代步数</span>
                <input
                  type="number"
                  min={1}
                  value={model.form.maxIterations}
                  onChange={event => model.updateForm({ maxIterations: event.target.value })}
                />
              </label>
              <label>
                <span>最大并发执行会话数</span>
                <input
                  type="number"
                  min={1}
                  value={model.form.maxConcurrentRuns}
                  onChange={event => model.updateForm({ maxConcurrentRuns: event.target.value })}
                />
              </label>
              <label>
                <span>上下文压缩触发比例（0 ~ 1）</span>
                <input
                  type="number"
                  step="0.05"
                  min="0.1"
                  max="0.99"
                  value={model.form.compressThresholdRatio}
                  onChange={event => model.updateForm({ compressThresholdRatio: event.target.value })}
                />
              </label>
              <label>
                <span>记忆提取最少累计轮数</span>
                <input
                  type="number"
                  min={1}
                  value={model.form.memoryMinTurns}
                  onChange={event => model.updateForm({ memoryMinTurns: event.target.value })}
                />
              </label>
              <label>
                <span>记忆闲置提取等待（秒）</span>
                <input
                  type="number"
                  min={0}
                  value={model.form.memoryIdleSeconds}
                  onChange={event => model.updateForm({ memoryIdleSeconds: event.target.value })}
                />
              </label>
            </div>

            <label className="settings-checkbox-row">
              <input
                type="checkbox"
                checked={model.form.planEnabled}
                onChange={event => model.updateForm({ planEnabled: event.target.checked })}
              />
              <span>默认开启 Plan 规划执行模式（Web 对话输入框仍可按会话单独切换）</span>
            </label>

            <div className="settings-form-footer">
              <button type="submit" className="settings-btn-primary" disabled={model.savingConfig}>
                <Save size={15} aria-hidden="true" />
                {model.savingConfig ? '正在保存…' : '保存并立即热生效'}
              </button>
              {model.dirty && (
                <button type="button" disabled={model.savingConfig} onClick={model.resetForm}>
                  放弃未保存修改
                </button>
              )}
            </div>
          </form>
        </section>

        {/* 3. MCP 服务与工具状态区 */}
        <section className="settings-card" id="settings-mcp" aria-labelledby="heading-mcp">
          <div className="settings-card-header">
            <div className="settings-card-title">
              <Blocks size={19} aria-hidden="true" />
              <div>
                <h2 id="heading-mcp">MCP 服务与工具目录</h2>
                <p>查看常驻 MCP 服务连接状态与已发现工具目录，修改配置后支持一键热重连，无需重启服务进程。</p>
              </div>
            </div>
            <div className="settings-actions">
              {model.mcpStatus && (
                <span className={`settings-badge ${model.mcpStatus.status}`}>
                  {MCP_STATUS_LABEL[model.mcpStatus.status] || model.mcpStatus.status}
                  {model.mcpStatus.total_tools > 0 ? ` · ${model.mcpStatus.total_tools} 个工具` : ''}
                </span>
              )}
              <button
                type="button"
                disabled={model.reconnectingMcp}
                onClick={() => void model.reconnectMcp()}
              >
                <RotateCcw size={15} aria-hidden="true" className={model.reconnectingMcp ? 'spin' : undefined} />
                {model.reconnectingMcp ? '正在重连…' : '重新连接 MCP'}
              </button>
            </div>
          </div>

          {model.mcpStatus && model.mcpStatus.servers.length > 0 ? (
            <div className="mcp-status-list">
              {model.mcpStatus.servers.map(srv => (
                <details key={srv.label} className="mcp-server-status-item">
                  <summary>
                    <div className="mcp-summary-main">
                      <strong>{srv.label}</strong>
                      <span className="mcp-transport-tag">{srv.transport}</span>
                      <span className={`settings-badge ${srv.status}`}>
                        {MCP_STATUS_LABEL[srv.status] || srv.status}
                      </span>
                      <span className="muted">{srv.tool_count} 个可检索工具</span>
                    </div>
                    <ChevronDown size={15} aria-hidden="true" />
                  </summary>
                  <div className="mcp-server-detail">
                    {srv.error && <p className="mcp-error-text" role="alert">连接错误：{srv.error}</p>}
                    {srv.description && <p className="muted">{srv.description}</p>}
                    {srv.tools.length > 0 ? (
                      <div className="mcp-tools-grid">
                        {srv.tools.map(tool => (
                          <div key={tool.name} className="mcp-tool-chip">
                            <code>{tool.name}</code>
                            <span>{tool.description || '无描述'}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="muted">
                        {srv.status === 'not_connected'
                          ? '服务尚未建立常驻连接，可点击右上角「重新连接 MCP」立即连接并发现工具。'
                          : '暂无可用工具。'}
                      </p>
                    )}
                  </div>
                </details>
              ))}
            </div>
          ) : (
            <p className="settings-empty-hint">暂未配置任何 MCP 服务，可在下方添加 stdio 或 SSE 服务。</p>
          )}

          <div className="mcp-config-editor">
            <div className="mcp-editor-toolbar">
              <label className="mcp-mode-select">
                <span>启用策略：</span>
                <select
                  aria-label="MCP 启用策略"
                  value={model.mcpEnabledMode}
                  onChange={event => model.setMcpEnabledMode(event.target.value as 'auto' | 'enabled' | 'disabled')}
                >
                  <option value="auto">自动（配置了服务即启用）</option>
                  <option value="enabled">显式启用</option>
                  <option value="disabled">全局禁用 MCP</option>
                </select>
              </label>
              <button type="button" onClick={model.addMcpServer}>
                <Plus size={14} aria-hidden="true" />添加 MCP 服务
              </button>
            </div>

            {model.mcpServers.length > 0 && (
              <div className="mcp-draft-list">
                {model.mcpServers.map(item => (
                  <div key={item.id} className="mcp-draft-row">
                    <div className="settings-grid-three">
                      <label>
                        <span>服务标签（Label）</span>
                        <input
                          value={item.label}
                          onChange={event => model.updateMcpServer(item.id, { label: event.target.value })}
                          placeholder="如 filesystem、codegraph"
                        />
                      </label>
                      <label>
                        <span>传输方式（Transport）</span>
                        <select
                          value={item.transport}
                          onChange={event => model.updateMcpServer(item.id, { transport: event.target.value as 'stdio' | 'sse' })}
                        >
                          <option value="stdio">stdio（本地子进程）</option>
                          <option value="sse">sse（远程 HTTP 流）</option>
                        </select>
                      </label>
                      {item.transport === 'stdio' ? (
                        <label>
                          <span>启动命令（Command）</span>
                          <input
                            value={item.command}
                            onChange={event => model.updateMcpServer(item.id, { command: event.target.value })}
                            placeholder="如 npx、uvx、python"
                          />
                        </label>
                      ) : (
                        <label>
                          <span>SSE 地址（URL）</span>
                          <input
                            value={item.url}
                            onChange={event => model.updateMcpServer(item.id, { url: event.target.value })}
                            placeholder="如 http://127.0.0.1:8080/sse"
                          />
                        </label>
                      )}
                    </div>
                    <div className="mcp-draft-second-row">
                      {item.transport === 'stdio' && (
                        <label>
                          <span>命令参数（Args，空格分隔）</span>
                          <input
                            value={item.argsText}
                            onChange={event => model.updateMcpServer(item.id, { argsText: event.target.value })}
                            placeholder="如 -y @modelcontextprotocol/server-filesystem ."
                          />
                        </label>
                      )}
                      <label>
                        <span>能力说明（可选，注入系统提示词引导模型何时使用）</span>
                        <input
                          value={item.description}
                          onChange={event => model.updateMcpServer(item.id, { description: event.target.value })}
                          placeholder="简述该服务的用途与适用场景"
                        />
                      </label>
                      <button
                        type="button"
                        className="settings-btn-danger-ghost"
                        aria-label={`移除服务 ${item.label}`}
                        onClick={() => model.removeMcpServer(item.id)}
                      >
                        <Trash2 size={14} aria-hidden="true" />移除
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="settings-form-footer">
              <button
                type="button"
                className="settings-btn-primary"
                disabled={model.reconnectingMcp || model.savingConfig}
                onClick={() => void model.saveAndReconnectMcp()}
              >
                <Save size={15} aria-hidden="true" />
                {model.reconnectingMcp ? '正在保存并重连…' : '保存 MCP 配置并重连'}
              </button>
            </div>
          </div>
        </section>

        {/* 4. 已安装技能（Skills）区 */}
        <section className="settings-card" id="settings-skills" aria-labelledby="heading-skills">
          <div className="settings-card-header">
            <div className="settings-card-title">
              <Sparkles size={19} aria-hidden="true" />
              <div>
                <h2 id="heading-skills">已安装技能（Skills · {model.activeWorkspace}）</h2>
                <p>兼容 SKILL.md 规范的工作空间专属工作流技能，Agent 会在匹配任务时通过 use_skill 按需加载执行。</p>
              </div>
            </div>
            <span className="settings-badge neutral">{model.skills.length} 个技能</span>
          </div>

          {model.skills.length > 0 ? (
            <div className="skills-grid">
              {model.skills.map(skill => (
                <div key={skill.name} className="skill-card">
                  <div className="skill-card-head">
                    <strong>{skill.name}</strong>
                    <span className="skill-version">v{skill.version}</span>
                  </div>
                  <p>{skill.description || '暂无技能描述'}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="skills-empty">
              <p>当前工作空间（<code>{model.activeWorkspace}</code>）暂未安装自定义技能。</p>
              <span>
                可将包含 <code>SKILL.md</code> 的技能文件夹放入当前工作空间的 <code>skills/</code> 目录，或让 Agent 协助创建专属技能。
              </span>
            </div>
          )}
        </section>
      </div>
    </>
  )
}
