const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')

test('伙伴随窗口停靠且只有一个实例，空会话仍展示欢迎伙伴', () => {
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
    useEffect: effect => effects.push(effect), useMemo: factory => factory(),
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
    assert.equal(nodes.filter(node => node.type === 'AgentCompanion').length, 1)
    return type => nodes.find(node => node.type === type)
  }
  let view = render()
  assert.equal(view('Sidebar').props.companion.props.activity, chat.activity)
  assert.equal(view('Topbar').props.companion, null)
  const cleanup = effects[0]()
  media.matches = true
  resize()
  view = render()
  assert.equal(view('Sidebar').props.companion, null)
  assert.equal(view('Topbar').props.companion.props.activity, chat.activity)
  chat.timeline = []
  view = render()
  assert.equal(view('AgentCompanion').props.compact, false)
  assert.equal(view('Topbar').props.companion, null)
  cleanup()
  assert.equal(resize, null)
})
