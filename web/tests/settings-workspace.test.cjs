const { test } = require('node:test')
const assert = require('node:assert/strict')
const { loadModule, hookRuntime, collectNodes, textOf } = require('./helpers/load.cjs')

const withoutBaseUrl = source => source.replace(/^const baseUrl = .*$/m, "const baseUrl = ''")

function makeMockConfig(overrides = {}) {
  return {
    has_api_key: true,
    api_key_masked: '****9876',
    env_overrides_active: ['llm.base_url'],
    llm: {
      provider: 'litellm',
      model: 'gpt-4o',
      base_url: 'https://api.openai.com/v1',
      api_base: '',
      proxy_url: '',
      max_tokens: 485000,
      has_api_key: true,
      api_key_masked: '****9876',
      ...(overrides.llm || {}),
    },
    agent: {
      name: 'QRClaw',
      max_iterations: 100,
      plan_enabled: false,
      max_concurrent_runs: 4,
      ...(overrides.agent || {}),
    },
    compress: {
      threshold_ratio: 0.9,
      recent_max_tokens: null,
      ...(overrides.compress || {}),
    },
    memory: {
      init_threshold: 5000,
      update_interval: 5000,
      min_turns: 3,
      idle_seconds: 120,
      cooldown_seconds: 300,
    },
    search: {
      has_tavily_api_key: false,
      tavily_api_key_masked: '',
    },
    heartbeat: { enabled: true, interval: 3600 },
    mcp: {
      enabled: null,
      active: false,
      servers: [],
      ...(overrides.mcp || {}),
    },
  }
}

function createFakeSettingsApi() {
  let active = 'default'
  let cfg = makeMockConfig()
  let mcp = {
    enabled: true,
    configured_enabled: true,
    status: 'not_connected',
    error: null,
    total_servers: 1,
    connected_servers: 0,
    total_tools: 0,
    servers: [{ label: 'codegraph', transport: 'stdio', status: 'not_connected', error: null, tool_count: 0, tools: [] }],
  }
  const workspaces = new Map([
    ['default', { agent_id: 'default', is_active: true, path: '/tmp/agents/default', session_count: 2, wiki_page_count: 3, skill_count: 1, sandbox_enabled: false }],
    ['rea', { agent_id: 'rea', is_active: false, path: '/tmp/agents/rea', session_count: 0, wiki_page_count: 0, skill_count: 0, sandbox_enabled: true }],
  ])
  const skillsByWs = {
    default: [{ name: 'analyze-repo', description: '代码仓库结构分析', version: '1.0.0' }],
    rea: [],
  }
  const patches = []
  let headerWorkspace = ''

  return {
    patches,
    getHeaderWorkspace: () => headerWorkspace,
    setWorkspace(id) { headerWorkspace = id },
    async getSystemConfig() { return structuredClone(cfg) },
    async updateSystemConfig(patch) {
      patches.push(structuredClone(patch))
      cfg = makeMockConfig({
        llm: { ...cfg.llm, ...(patch.llm || {}) },
        agent: { ...cfg.agent, ...(patch.agent || {}) },
        mcp: { ...cfg.mcp, ...(patch.mcp || {}) },
      })
      if (patch.clear_api_key) {
        cfg.has_api_key = false
        cfg.api_key_masked = ''
        cfg.llm.has_api_key = false
        cfg.llm.api_key_masked = ''
      } else if (patch.llm?.api_key) {
        cfg.has_api_key = true
        cfg.api_key_masked = `****${patch.llm.api_key.slice(-4)}`
        cfg.llm.has_api_key = true
        cfg.llm.api_key_masked = cfg.api_key_masked
      }
      return structuredClone(cfg)
    },
    async getMcpStatus() { return structuredClone(mcp) },
    async reconnectMcp() {
      mcp = {
        ...mcp,
        status: 'connected',
        connected_servers: 1,
        total_tools: 2,
        servers: [{
          label: 'codegraph',
          transport: 'stdio',
          status: 'connected',
          error: null,
          tool_count: 2,
          tools: [
            { name: 'search_symbol', description: '搜索代码符号' },
            { name: 'find_callers', description: '查找调用链' },
          ],
        }],
      }
      return structuredClone(mcp)
    },
    async listWorkspaces() {
      const items = [...workspaces.values()].map(w => ({ ...w, is_active: w.agent_id === active }))
      return { active_workspace: active, workspaces: items }
    },
    async createWorkspace(agentId, switchImmediate = false) {
      const item = { agent_id: agentId, is_active: switchImmediate, path: `/tmp/agents/${agentId}`, session_count: 0, wiki_page_count: 0, skill_count: 0, sandbox_enabled: true }
      workspaces.set(agentId, item)
      skillsByWs[agentId] = []
      if (switchImmediate) active = agentId
      return { ...item, active_workspace: active }
    },
    async switchWorkspace(agentId) {
      active = agentId
      headerWorkspace = agentId
      const item = workspaces.get(agentId)
      return { ...item, is_active: true, active_workspace: active }
    },
    async deleteWorkspace(agentId) {
      workspaces.delete(agentId)
      return { deleted: true, agent_id: agentId, active_workspace: active }
    },
    async listSkills() {
      return structuredClone(skillsByWs[active] || [])
    },
  }
}

function mountSettingsHook(api, options = {}) {
  const runtime = hookRuntime()
  let visible = true
  const { useSettingsWorkspace } = loadModule('src/features/settings/useSettingsWorkspace.ts', {
    mocks: { react: runtime.react },
    globals: { AbortController },
  })
  const render = () => runtime.render(() => useSettingsWorkspace(api, visible, options))
  render()
  return {
    render,
    setVisible(v) { visible = v; return render() },
    async flush() {
      let view
      for (let i = 0; i < 4; i++) {
        await new Promise(resolve => setImmediate(resolve))
        view = render()
      }
      return view
    },
    close: runtime.close,
  }
}

test('配置加载与保存：未填写新密码时不发送 api_key 覆盖旧密钥，填写新密码时正确更新', async () => {
  const api = createFakeSettingsApi()
  const hook = mountSettingsHook(api)
  let view = await hook.flush()

  assert.equal(view.config.api_key_masked, '****9876')
  assert.equal(view.form.model, 'gpt-4o')
  assert.equal(view.form.apiKeyInput, '')

  // 修改模型但不填新 API Key
  view.updateForm({ model: 'deepseek/deepseek-chat', maxTokens: '128000' })
  view = hook.render()
  await view.saveConfig()
  view = await hook.flush()

  assert.equal(api.patches.length, 1)
  assert.equal(api.patches[0].llm.model, 'deepseek/deepseek-chat')
  assert.equal(api.patches[0].llm.max_tokens, 128000)
  assert.equal(Object.hasOwn(api.patches[0].llm, 'api_key'), false, '未输入新密钥时绝不携带 api_key')
  assert.equal(view.config.api_key_masked, '****9876')
  assert.match(view.notice, /立即生效/)

  // 填写新密钥后保存
  view.updateForm({ apiKeyInput: 'sk-brand-new-1122' })
  view = hook.render()
  await view.saveConfig()
  view = await hook.flush()

  assert.equal(api.patches[1].llm.api_key, 'sk-brand-new-1122')
  assert.equal(view.config.api_key_masked, '****1122')
  assert.equal(view.form.apiKeyInput, '', '保存成功后清空密码输入框')
  hook.close()
})

test('工作空间切换与 API 请求头联动刷新会话列表及技能列表', async () => {
  const requests = []
  const apiMod = loadModule('src/lib/api.ts', {
    transform: withoutBaseUrl,
    globals: {
      TextDecoder,
      fetch: async (url, options) => {
        requests.push({ url, options })
        if (url === '/v1/system/workspaces/switch') {
          return { ok: true, status: 200, json: async () => ({ agent_id: 'rea', is_active: true, active_workspace: 'rea' }) }
        }
        return { ok: true, status: 200, json: async () => [] }
      },
    },
  })
  const realClient = apiMod.createApiClient()
  await realClient.listSessions()
  assert.equal(requests[0].options, undefined, '默认未显式切换时不污染现有请求参数')

  await realClient.switchWorkspace('rea')
  assert.equal(realClient.getWorkspace(), 'rea')
  await realClient.listSessions()
  assert.equal(requests[2].options.headers['X-QRClaw-Workspace'], 'rea', '切换工作空间后自动携带 X-QRClaw-Workspace 请求头')

  // 验证 useSettingsWorkspace 切换工作空间触发主应用联动回调与技能刷新
  const fakeApi = createFakeSettingsApi()
  const switchedTo = []
  const hook = mountSettingsHook(fakeApi, { onWorkspaceSwitched: id => switchedTo.push(id) })
  let view = await hook.flush()
  assert.equal(view.activeWorkspace, 'default')
  assert.equal(view.skills.length, 1)

  await view.switchWorkspace('rea')
  view = await hook.flush()
  assert.deepEqual(switchedTo, ['rea'])
  assert.equal(view.activeWorkspace, 'rea')
  assert.equal(fakeApi.getHeaderWorkspace(), 'rea')
  assert.equal(view.skills.length, 0, '切换到 rea 后展示其独立技能列表')
  hook.close()
})

test('MCP 重连与服务增删保存后立即更新已连接状态与工具目录', async () => {
  const api = createFakeSettingsApi()
  const hook = mountSettingsHook(api)
  let view = await hook.flush()

  assert.equal(view.mcpStatus.status, 'not_connected')
  assert.equal(view.mcpStatus.total_tools, 0)

  await view.reconnectMcp()
  view = await hook.flush()
  assert.equal(view.mcpStatus.status, 'connected')
  assert.equal(view.mcpStatus.total_tools, 2)
  assert.equal(view.mcpStatus.servers[0].tools[0].name, 'search_symbol')

  // 新增一个 SSE MCP 服务并保存重连
  view.addMcpServer()
  view = hook.render()
  const addedId = view.mcpServers[0].id
  view.updateMcpServer(addedId, { label: 'docs-sse', transport: 'sse', url: 'http://127.0.0.1:9000/sse', description: '文档检索' })
  view = hook.render()
  await view.saveAndReconnectMcp()
  view = await hook.flush()

  assert.equal(api.patches.at(-1).mcp.servers[0].label, 'docs-sse')
  assert.equal(api.patches.at(-1).mcp.servers[0].url, 'http://127.0.0.1:9000/sse')
  hook.close()
})

test('打开设置页与切回当前对话保留聊天草稿，顶栏和侧栏正确呈现工作空间与设置标题', () => {
  const runtime = hookRuntime()
  const media = { matches: false, addEventListener() {}, removeEventListener() {} }
  let draftValue = '正在编写的半截提示词草稿'
  const chat = {
    conversations: [{ id: 's1', title: '架构讨论', messageCount: 2, updatedAt: '', totalTokens: 0 }],
    activeId: 's1',
    session: { id: 's1', message_count: 2 },
    timeline: [{}],
    messages: [],
    running: false,
    queued: false,
    loading: false,
    creating: false,
    mutating: false,
    runningIds: new Set(),
    unreadIds: new Set(),
    mutatingIds: new Set(),
    stats: { compressions: [], toolTotal: 0, toolList: [], memoryWrites: 0, memoryPages: [] },
    activity: { phase: 'idle', status: 'idle', label: '可以开始' },
    draft: draftValue,
    setDraft(next) { draftValue = next; chat.draft = next },
    planEnabled: false,
    togglePlan() {},
    send() {},
    loadConversation() {},
    newConversation() {},
    renameConversation() {},
    deleteConversation() {},
  }

  const appExports = loadModule('src/app/App.tsx', {
    globals: {
      window: { location: { hash: '' }, matchMedia: () => media, addEventListener() {}, removeEventListener() {} },
      document: { documentElement: { dataset: {}, removeAttribute() {} }, getElementById: () => null },
      localStorage: { getItem: () => null, setItem() {} },
    },
    mocks(name) {
      if (name === 'react') return runtime.react
      if (name.includes('useChatWorkspace')) return { useChatWorkspace: () => chat }
      if (name.includes('/api')) return { createApiClient: () => ({}) }
      if (name.startsWith('../')) {
        const comp = name.split('/').at(-1)
        return { [comp]: comp, MemoryWorkspace: 'MemoryWorkspace' }
      }
    },
  })

  let tree = runtime.render(() => appExports.App())
  let nodes = collectNodes(tree)
  let sidebarNode = nodes.find(n => n.type === 'Sidebar')
  assert.equal(sidebarNode.props.settingsOpen, false)
  assert.equal(sidebarNode.props.workspaceId, 'default')
  assert.equal(nodes.find(n => n.type === 'Composer').props.value, '正在编写的半截提示词草稿')

  // 点击进入系统设置
  sidebarNode.props.onOpenSettings()
  tree = runtime.render(() => appExports.App())
  nodes = collectNodes(tree)
  assert.equal(tree.props['data-view'], 'settings')
  assert.equal(nodes.find(n => n.type === 'Topbar').props.settingsOpen, true)
  const settingsViewNode = nodes.find(n => n.type === 'SettingsView')
  assert.equal(settingsViewNode.props.visible, true)

  // 模拟在设置页切换到工作空间 rea
  settingsViewNode.props.onWorkspaceSwitched('rea')
  tree = runtime.render(() => appExports.App())
  nodes = collectNodes(tree)
  assert.equal(nodes.find(n => n.type === 'Sidebar').props.workspaceId, 'rea')

  // 从设置页切回当前对话，草稿丝毫无损
  nodes.find(n => n.type === 'Sidebar').props.onOpenChat()
  tree = runtime.render(() => appExports.App())
  nodes = collectNodes(tree)
  assert.equal(tree.props['data-view'], 'chat')
  assert.equal(nodes.find(n => n.type === 'Composer').props.value, '正在编写的半截提示词草稿')
  runtime.close()

  // 验证真实 Sidebar 与 Topbar 渲染系统设置导航、工作空间标识与顶栏标题
  const { Sidebar } = loadModule('src/components/Sidebar.tsx', {
    mocks: name => name.includes('ConversationList') ? { ConversationList: 'ConversationList' }
      : name.includes('AgentSprite') ? { AgentSprite: 'AgentSprite' } : undefined,
  })
  const sidebarDom = collectNodes(Sidebar({
    conversations: [], activeId: 's1', runningIds: new Set(), unreadIds: new Set(), mutatingIds: new Set(),
    open: false, creating: false, memoryOpen: false, settingsOpen: true, workspaceId: 'rea', activity: null,
    onOpenMemory() {}, onOpenChat() {}, onClose() {}, onNewChat() {},
    onSelectConversation() {}, onRenameConversation() {}, onDeleteConversation() {},
  }))
  const navSettings = sidebarDom.find(n => n.type === 'button' && textOf(n).includes('系统设置'))
  assert.ok(navSettings, '侧栏渲染系统设置按钮')
  assert.equal(navSettings.props['aria-current'], 'page')
  const footerWs = sidebarDom.find(n => n.props.className === 'sidebar-workspace-btn')
  assert.equal(textOf(footerWs), 'rea')

  const { Topbar } = loadModule('src/components/Topbar.tsx')
  const topbarDom = Topbar({
    title: '架构讨论', memoryOpen: false, settingsOpen: true, companion: null,
    sidebarOpen: false, inspectorOpen: false, onToggleSidebar() {}, onToggleInspector() {},
  })
  assert.ok(textOf(topbarDom).includes('设置›模型、工作空间与能力状态'))
  assert.ok(!collectNodes(topbarDom).some(n => n.props.className === 'inspector-toggle'), '设置页不显示聊天专属属性按钮')
})
