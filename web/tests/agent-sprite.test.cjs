const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

/** 用现有 TypeScript 编译器加载真实引擎和组件，不安装测试专用运行时。 */
function loadSource(file, globals = {}, cache = new Map()) {
  file = path.resolve(file)
  if (cache.has(file)) return cache.get(file)
  const exports = {}
  cache.set(file, exports)
  const compiled = ts.transpileModule(readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  vm.runInNewContext(compiled, { exports, ...globals, require: name => {
    if (name === 'react' && globals.react) return globals.react
    return name.startsWith('.') ? loadSource(path.resolve(path.dirname(file), `${name}.ts`), globals, cache) : require(name)
  } }, { filename: file })
  return exports
}

test('所有接入状态在连续运行和快速切换时输出有效、可重现的 SVG', () => {
  const cache = new Map()
  const { BotEngine } = loadSource('src/vendor/bloub/engine.ts', {}, cache)
  const { STATES } = loadSource('src/vendor/bloub/states.ts', {}, cache)
  const engine = new BotEngine(100)
  let now = 0
  for (const { id: state } of STATES) {
    engine.setState(state, now)
    for (const offset of [0, 0.05, 0.8, 2, 5, 60]) {
      const frame = engine.sample(now + offset)
      const serialized = JSON.stringify(frame)
      assert.ok(frame.bodyPath.startsWith('M'))
      assert.doesNotMatch(serialized, /NaN|Infinity/)
      assert.equal(serialized, JSON.stringify(engine.sample(now + offset)))
    }
    now += 0.1
  }
})

/** 模拟时钟和系统事件，检查真实组件的动画生命周期。 */
function sprite() {
  const slots = [], pending = [], frames = new Map(), visibility = new Map(), preferences = new Map()
  let cursor = 0, now = 0, serial = 0
  const motion = { matches: false, addEventListener: (name, fn) => preferences.set(name, fn), removeEventListener: name => preferences.delete(name) }
  const document = { hidden: false, addEventListener: (name, fn) => visibility.set(name, fn), removeEventListener: name => visibility.delete(name) }
  const react = {
    useId: () => 'sprite-test',
    useState(initial) {
      const index = cursor++
      if (!slots[index]) slots[index] = { value: typeof initial === 'function' ? initial() : initial }
      return [slots[index].value, value => { slots[index].value = value }]
    },
    useEffect(fn, deps) {
      const index = cursor++
      if (!slots[index] || deps.some((dep, i) => slots[index].deps[i] !== dep)) pending.push(() => {
        slots[index]?.cleanup?.()
        slots[index] = { deps, cleanup: fn() }
      })
    },
  }
  const { AgentSprite } = loadSource('src/components/AgentSprite.tsx', {
    react, window: { matchMedia: () => motion }, document,
    performance: { now: () => now }, requestAnimationFrame: fn => { frames.set(++serial, fn); return serial }, cancelAnimationFrame: id => frames.delete(id),
  })
  return {
    frames, visibility, preferences,
    render(phase) { cursor = 0; const tree = AgentSprite({ phase }); pending.splice(0).forEach(fn => fn()); return tree },
    tick(time) { now = time; const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(fn => fn(now)) },
    hide(hidden) { document.hidden = hidden; visibility.get('visibilitychange')?.() },
    reduce(matches) { motion.matches = matches; preferences.get('change')?.() },
    frame: () => JSON.stringify(slots[1].value),
    close() { slots.forEach(slot => slot.cleanup?.()) },
  }
}

test('精灵暂停隐藏页面、遵循减少动态效果，卸载后释放帧和监听器', () => {
  const view = sprite()
  view.render('thinking')
  view.tick(100)
  const before = view.frame()
  view.tick(700)
  assert.notEqual(view.frame(), before)
  view.hide(true)
  assert.equal(view.frames.size, 0)
  view.hide(false)
  assert.equal(view.frames.size, 1)
  view.reduce(true)
  assert.equal(view.frames.size, 0)
  const still = view.frame()
  view.tick(3000)
  assert.equal(view.frame(), still)
  const tree = view.render('error')
  assert.equal(tree.props['data-animation'], 'alert')
  assert.equal(tree.props['aria-hidden'], 'true')
  assert.notEqual(view.frame(), still, '静态模式也必须更新工作阶段')
  assert.equal(view.frames.size, 0)
  view.reduce(false)
  assert.equal(view.frames.size, 1)
  view.close()
  assert.equal(view.frames.size, 0)
  assert.equal(view.visibility.size, 0)
  assert.equal(view.preferences.size, 0)
})

test('长时间工具任务会重新播放轨道动画，完成后只保留一个动画循环', () => {
  const view = sprite()
  view.render('tool')
  view.tick(1000)
  assert.ok(JSON.parse(view.frame()).arcs.length > 0)
  view.tick(3900)
  view.tick(4500)
  view.tick(5500)
  assert.ok(JSON.parse(view.frame()).arcs.length > 0, '单次轨道结束后必须能继续表示工作中')
  view.render('completed')
  view.tick(6500)
  assert.ok(JSON.parse(view.frame()).notif)
  assert.equal(view.frames.size, 1)
  view.close()
})
