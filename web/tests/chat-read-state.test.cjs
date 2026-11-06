const { test } = require('node:test')
const assert = require('node:assert/strict')
const { loadModule } = require('./helpers/load.cjs')

/** 用可控可见区域驱动真实面板的阅读观察器，不借助计时自动清除提示。 */
function panel(overrides = {}) {
  const exports = {}, effects = [], acknowledgements = [], listeners = new Map()
  const history = { clientHeight: 500, scrollHeight: 1000, scrollTop: 0, getBoundingClientRect: () => ({ top: 0, bottom: 500 }) }
  let markerTop = 499, refIndex = 0, observer, disconnected = false
  const marker = { getBoundingClientRect: () => ({ top: markerTop, bottom: markerTop + 1 }) }
  const document = { visibilityState: 'visible', focused: true, hasFocus: () => document.focused,
    addEventListener: (key, value) => listeners.set(key, value), removeEventListener: key => listeners.delete(key) }
  Object.assign(exports, loadModule('src/features/chat/ChatPanel.tsx', {
    globals: { document,
      window: { addEventListener: document.addEventListener, removeEventListener: document.removeEventListener },
      IntersectionObserver: class {
        constructor(callback, options) { observer = callback; assert.equal(options.root, history) }
        observe(node) { assert.equal(node, marker) }
        disconnect() { disconnected = true }
      } },
    mocks: {
      react: {
        useRef: initial => ({ current: refIndex++ === 0 ? history : refIndex === 2 ? marker : initial }),
        useState: initial => [initial, () => {}], useLayoutEffect: () => {}, useEffect: fn => effects.push(fn),
      },
      'react-markdown': {}, 'remark-gfm': {}, './ExecutionReport': {},
    },
  }))
  exports.ChatPanel({ sessionId: 'A', timeline: [], visible: true, unreadReplyId: 'reply-A',
    running: false, loading: false, mutating: false, onReplyRead: (...args) => acknowledgements.push(args), ...overrides })
  const cleanups = effects.map(fn => fn())
  return { acknowledgements, document, listeners,
    intersect(visible, ratio = 1) { observer?.([{ isIntersecting: visible, intersectionRatio: visible ? ratio : 0 }]) },
    moveMarker(top) { markerTop = top },
    close() { cleanups.forEach(fn => fn?.()); assert.equal(listeners.size, 0); if (observer) assert.equal(disconnected, true) },
  }
}

test('只有前台可见的最新回复末尾才确认已读，阅读旧消息和后台标签页保留未读', () => {
  const view = panel()
  view.intersect(false)
  assert.equal(view.acknowledgements.length, 0)
  view.document.visibilityState = 'hidden'
  view.intersect(true)
  assert.equal(view.acknowledgements.length, 0)
  view.document.visibilityState = 'visible'
  view.document.focused = false
  view.listeners.get('visibilitychange')()
  assert.equal(view.acknowledgements.length, 0)
  view.document.focused = true
  view.moveMarker(800)
  view.listeners.get('focus')()
  assert.equal(view.acknowledgements.length, 0, '焦点恢复时不能信任旧的相交结果')
  view.moveMarker(499.25)
  view.intersect(true, 0.75)
  assert.deepEqual(view.acknowledgements, [['A', 'reply-A']])
  view.close()
  view.intersect(true)
  assert.equal(view.acknowledgements.length, 1, '卸载后排队的可见回调不能确认阅读')
})

test('记忆页或遮挡面板、运行中、载入中和已读状态都不启动阅读确认', () => {
  for (const overrides of [{ visible: false }, { running: true }, { loading: true }, { mutating: true }, { unreadReplyId: null }]) {
    const view = panel(overrides)
    view.intersect(true)
    assert.equal(view.acknowledgements.length, 0)
    view.close()
  }
})
