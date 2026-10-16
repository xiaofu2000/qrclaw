const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')

/** 驱动真实悬浮组件，验证拖动、键盘与窗口边界，不运行动画引擎。 */
function floating(corner = 'right') {
  const slots = [], effects = [], listeners = {}, exports = {}
  let cursor = 0
  const window = { innerWidth: 1280, innerHeight: 720,
    addEventListener: (name, listener) => { listeners[name] = listener },
    removeEventListener: name => { delete listeners[name] } }
  const source = ts.transpileModule(readFileSync('src/components/FloatingCompanion.tsx', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  vm.runInNewContext(source, { exports, window, require(name) {
    if (name === 'react') return {
      useState(initial) { const i = cursor++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial }; return [slots[i].value, value => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value }] },
      useRef(initial) { return slots[cursor++] ??= { current: initial } },
      useLayoutEffect: effect => effects.push(effect),
    }
    if (name === './AgentSprite') return { AgentSprite: 'AgentSprite' }
    return require(name)
  } })
  const activity = { phase: 'thinking' }
  const surfaceRef = { current: { getBoundingClientRect: () => ({ left: window.innerWidth > 1100 ? 240 : 220, right: window.innerWidth - 16 }) } }
  return { window, listeners, effects, activity, render(nextCorner = corner) {
    corner = nextCorner
    cursor = 0
    return exports.FloatingCompanion({ activity, sessionId: 'demo', corner, surfaceRef }).props
  } }
}

test('桌面卡片默认在顶部，拖动不会越界，取消拖动后不继续移动', () => {
  const view = floating(), captured = new Set()
  const target = { setPointerCapture: id => captured.add(id), hasPointerCapture: id => captured.has(id), releasePointerCapture: id => captured.delete(id) }
  let props = view.render()
  assert.equal(props.style.top, 16)
  assert.equal(props.style.left, 1144)
  assert.equal(props.style.width, 104)
  props.onPointerDown({ button: 0, pointerId: 1, clientX: 1150, clientY: 30, currentTarget: target })
  props.onPointerMove({ pointerId: 1, clientX: -500, clientY: 2000 })
  props = view.render()
  assert.equal(props.style.left, 16)
  assert.equal(props.style.top, 600)
  props.onPointerUp({ pointerId: 1, currentTarget: target })
  assert.equal(captured.size, 0)
  props.onPointerMove({ pointerId: 1, clientX: 400, clientY: 400 })
  assert.equal(view.render().style.left, 16)
  props.onPointerDown({ button: 0, pointerId: 2, clientX: 30, clientY: 610, currentTarget: target })
  props.onPointerCancel()
  props.onPointerMove({ pointerId: 2, clientX: 400, clientY: 400 })
  assert.equal(view.render().style.left, 16)
})

test('方向键移动、Home 回顶部，缩小窗口仍可见，卸载清理监听', () => {
  const view = floating()
  let props = view.render(), prevented = 0
  const cleanup = view.effects[0]()
  props.onKeyDown({ key: 'ArrowDown', preventDefault: () => prevented++ })
  props = view.render()
  assert.equal(props.style.top, 32)
  view.window.innerWidth = 900
  view.window.innerHeight = 500
  view.listeners.resize()
  props = view.render()
  assert.equal(props.style.left, 780)
  assert.equal(props.style.top, 32)
  props.onKeyDown({ key: 'Home', preventDefault: () => prevented++ })
  props = view.render()
  assert.equal(props.style.top, 16)
  assert.equal(props.style.left, 764)
  assert.equal(prevented, 2)
  view.activity.phase = 'tool'
  assert.equal(view.render().children.props.phase, 'tool')
  cleanup()
  assert.equal(view.listeners.resize, undefined)
})

test('聊天在工作面左上角，进入记忆和返回聊天均重置位置，Home 遵循当前页面', () => {
  const view = floating('left')
  let props = view.render()
  let cleanup = view.effects.at(-1)()
  assert.equal(props.style.left, 256)
  props.onKeyDown({ key: 'ArrowDown', preventDefault() {} })
  assert.equal(view.render().style.top, 32)
  cleanup()
  view.render('right')
  cleanup = view.effects.at(-1)()
  props = view.render()
  assert.equal(props.style.left, 1144)
  assert.equal(props.style.top, 16)
  cleanup()
  view.render('left')
  cleanup = view.effects.at(-1)()
  props = view.render()
  assert.equal(props.style.left, 256)
  props.onKeyDown({ key: 'ArrowRight', preventDefault() {} })
  props = view.render()
  assert.equal(props.style.left, 272)
  props.onKeyDown({ key: 'Home', preventDefault() {} })
  assert.equal(view.render().style.left, 256)
  view.window.innerWidth = 900
  view.listeners.resize()
  assert.equal(view.render().style.left, 236)
  cleanup()
})
