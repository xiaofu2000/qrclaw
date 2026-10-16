const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')

test('桌面伙伴与状态固定在侧栏顶部，手机布局保持不变，每种状态只有一个动画', () => {
  const states = [], effects = [], exports = {}
  let cursor = 0, resize
  const media = { matches: false, addEventListener: (_, listener) => { resize = listener }, removeEventListener: () => { resize = null } }
  const chat = { conversations: [], activeId: 'demo', timeline: [{}], messages: [], running: false,
    loading: false, runningIds: new Set(), activity: { phase: 'tool', status: 'busy', label: '正在使用工具' } }
  const source = ts.transpileModule(readFileSync('src/app/App.tsx', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  const react = {
    useState(initial) {
      const index = cursor++
      if (!(index in states)) states[index] = typeof initial === 'function' ? initial() : initial
      return [states[index], value => { states[index] = value }]
    },
    useEffect: effect => effects.push(effect), useMemo: factory => factory(), useRef: () => ({ current: null }),
  }
  vm.runInNewContext(source, { exports, window: { location: { hash: '' }, matchMedia: () => media }, require(name) {
    if (name === 'react') return react
    if (name.includes('useChatWorkspace')) return { useChatWorkspace: () => chat }
    if (name.includes('/api')) return { createApiClient: () => ({}) }
    if (name.startsWith('../')) {
      const component = name.split('/').at(-1)
      return { [component]: component, MemoryWorkspace: 'MemoryWorkspace' }
    }
    return require(name)
  } })
  /** 同时遍历子元素和组件插槽，防止 CSS 隐藏的重复动画漏检。 */
  function render() {
    cursor = 0
    const nodes = []
    function visit(node) {
      if (Array.isArray(node)) return node.forEach(visit)
      if (!node || typeof node !== 'object' || !node.props) return
      nodes.push(node)
      visit(node.props.children)
      visit(node.props.companion)
    }
    visit(exports.App())
    assert.equal(nodes.filter(node => node.type === 'Sidebar' && node.props.activity || node.type === 'AgentCompanion' && !node.props.textOnly).length, 1)
    return type => nodes.find(node => node.type === type)
  }
  let view = render()
  assert.equal(view('Sidebar').props.activity, chat.activity)
  assert.equal(view('Topbar').props.companion, null)
  const sidebar = view('Sidebar')
  sidebar.props.onOpenMemory()
  view = render()
  assert.equal(view('Sidebar').props.activity, chat.activity)
  assert.equal(view('Sidebar').props.activeId, sidebar.props.activeId)
  assert.equal(view('Topbar').props.companion, null)
  view('Sidebar').props.onOpenChat()
  const cleanup = effects[0]()
  media.matches = true
  resize()
  view = render()
  assert.equal(view('Sidebar').props.activity, null)
  assert.equal(view('Topbar').props.companion.props.activity, chat.activity)
  chat.timeline = []
  view = render()
  assert.equal(view('AgentCompanion').props.compact, false)
  assert.equal(view('Topbar').props.companion, null)
  media.matches = false
  resize()
  view = render()
  assert.equal(view('AgentCompanion').props.textOnly, true)
  assert.equal(view('Sidebar').props.activity, chat.activity)
  states[2] = true
  view = render()
  assert.equal(view('AgentCompanion'), undefined)
  assert.equal(view('Sidebar').props.activity, chat.activity)
  media.matches = true
  resize()
  view = render()
  assert.equal(view('Sidebar').props.activity, null)
  assert.equal(view('Topbar').props.companion.props.activity, chat.activity)
  cleanup()
  assert.equal(resize, null)
})

test('侧栏动画与相邻状态使用同一活动数据，手机抽屉不挂载重复动画', () => {
  const exports = {}
  const source = ts.transpileModule(readFileSync('src/components/Sidebar.tsx', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  vm.runInNewContext(source, { exports, require(name) {
    if (name.includes('ConversationList')) return { ConversationList: 'ConversationList' }
    if (name.includes('AgentSprite')) return { AgentSprite: 'AgentSprite' }
    return require(name)
  } })
  /** 遍历真实侧栏树，检查状态不是只传到组件而未被渲染。 */
  function render(activity, memoryOpen = false) {
    const nodes = []
    function visit(node) {
      if (Array.isArray(node)) return node.forEach(visit)
      if (!node || typeof node !== 'object' || !node.props) return
      nodes.push(node)
      visit(node.props.children)
    }
    visit(exports.Sidebar({ conversations: [], activeId: 'demo', activity, memoryOpen }))
    return nodes
  }
  for (const [phase, status, label] of [
    ['idle', 'idle', '可以开始'], ['thinking', 'busy', '正在思考'],
    ['tool', 'busy', '正在使用工具'], ['error', 'error', '遇到问题'],
  ]) {
    for (const memoryOpen of [false, true]) {
      const nodes = render({ phase, status, label }, memoryOpen)
      assert.equal(nodes.filter(node => node.type === 'AgentSprite').length, 1)
      assert.equal(nodes.find(node => node.type === 'AgentSprite').props.phase, phase)
      const liveStatus = nodes.find(node => node.props.role === 'status')
      assert.equal(liveStatus.props.children[1], label)
      assert.ok(liveStatus.props.className.includes(status))
    }
  }
  const mobileNodes = render(null)
  assert.equal(mobileNodes.filter(node => node.type === 'AgentSprite').length, 0)
  assert.equal(mobileNodes.filter(node => node.props.role === 'status').length, 0)
})
