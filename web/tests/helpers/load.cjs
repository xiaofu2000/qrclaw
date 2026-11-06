/**
 * 测试共用工具：编译并加载真实源码、驱动 Hook、遍历 JSX 元素树。
 * 不引入浏览器或测试框架依赖；各测试只描述替身和断言。
 */
const { existsSync, readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const root = path.resolve(__dirname, '../..')
const EXTENSIONS = ['', '.ts', '.tsx', '/index.ts', '/index.tsx']

/** 把相对导入解析成真实文件；找不到时返回 null。 */
function resolveFile(fromFile, request) {
  const base = path.resolve(path.dirname(fromFile), request)
  return EXTENSIONS.map(ext => base + ext).find(candidate => existsSync(candidate) && !candidate.endsWith('/')) ?? null
}

/**
 * 编译并加载 src 下的模块。相对导入递归加载真实源码（同一次加载共享缓存），CSS 返回空对象，
 * 其余包名走 Node 的 require。mocks 可以是 { 模块名: 导出 } 或 (模块名, 来源文件) => 导出 | undefined。
 */
function loadModule(file, { mocks = {}, globals = {}, transform = source => source, cache = new Map() } = {}) {
  const full = path.resolve(root, file)
  if (cache.has(full)) return cache.get(full)
  const exports = {}
  cache.set(full, exports)
  // 传入文件名，.ts 按 TypeScript 解析（泛型箭头函数不会被当成 JSX），.tsx 才启用 JSX。
  const output = ts.transpileModule(transform(readFileSync(full, 'utf8'), full), {
    fileName: full,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  const mock = typeof mocks === 'function' ? mocks : name => (Object.hasOwn(mocks, name) ? mocks[name] : undefined)
  vm.runInNewContext(output, { console, ...globals, exports, require(name) {
    const replaced = mock(name, full)
    if (replaced !== undefined) return replaced
    if (name.endsWith('.css')) return {}
    if (name.startsWith('.')) {
      const target = resolveFile(full, name)
      if (!target) throw new Error(`找不到 ${name}（来自 ${path.relative(root, full)}）`)
      return loadModule(target, { mocks, globals, transform, cache })
    }
    return require(name)
  } }, { filename: full })
  return exports
}

/**
 * 按调用顺序保存状态的最小 React Hook 运行器：useState / useRef / useMemo / useCallback / useEffect / useLayoutEffect / useId。
 * render 之后才执行本次登记的副作用，依赖未变时不重复执行，close 时执行全部清理。
 */
function hookRuntime() {
  const slots = []
  let cursor = 0, effects = []
  const changed = (before, after) => !before || !after || after.some((value, index) => value !== before[index])
  const effect = (fn, deps) => {
    const index = cursor++
    if (changed(slots[index]?.deps, deps)) effects.push(() => {
      slots[index]?.cleanup?.()
      slots[index] = { deps, cleanup: fn() }
    })
  }
  const react = {
    useState(initial) {
      const index = cursor++
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial }
      return [slots[index].value, value => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value }]
    },
    useRef(initial) { return (slots[cursor++] ??= { current: initial }) },
    useMemo(fn, deps) {
      const index = cursor++
      if (changed(slots[index]?.deps, deps)) slots[index] = { value: fn(), deps }
      return slots[index].value
    },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps) },
    useEffect: effect,
    useLayoutEffect: effect,
    useId: () => 'test-id',
  }
  return {
    react, slots,
    /** 执行一次渲染函数，然后运行本次登记的副作用。 */
    render(fn) {
      cursor = 0
      const result = fn()
      const pending = effects
      effects = []
      pending.forEach(run => run())
      return result
    },
    close() { slots.forEach(slot => slot?.cleanup?.()) },
  }
}

/** 收集 JSX 元素树里的全部元素，可额外遍历指定的插槽属性（如 companion）。 */
function collectNodes(tree, slotProps = []) {
  const nodes = []
  ;(function visit(node) {
    if (Array.isArray(node)) return node.forEach(visit)
    if (!node || typeof node !== 'object' || !node.props) return
    nodes.push(node)
    visit(node.props.children)
    for (const name of slotProps) visit(node.props[name])
  })(tree)
  return nodes
}

/** 拼出元素树里的全部文字。 */
function textOf(node) {
  const parts = []
  ;(function walk(value) {
    if (Array.isArray(value)) return value.forEach(walk)
    if (typeof value === 'string' || typeof value === 'number') parts.push(String(value))
    else if (value && typeof value === 'object' && value.props) walk(value.props.children)
  })(node)
  return parts.join('')
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

module.exports = { loadModule, hookRuntime, collectNodes, textOf, deferred }
