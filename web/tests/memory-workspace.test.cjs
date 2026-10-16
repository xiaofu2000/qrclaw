const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
const compiled = ts.transpileModule(readFileSync('src/features/memory/useMemoryWorkspace.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const page = (name, revision = 1) => ({ name, revision, content: name, description: '', tags: [], related: [], sources: [], links: [], backlinks: [], status: 'ready' })
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

// 驱动真实 Hook 的效果与网络完成顺序，验证响应竞态及后台轮询。
function workspace(api) {
  const slots = [], timers = new Map(), listeners = new Map()
  let cursor = 0, effects = [], visible = true, timerId = 0
  const changed = (before, after) => !before || after.some((value, index) => value !== before[index])
  const react = {
    useState(initial) { const i = cursor++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial }; return [slots[i].value, value => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value }] },
    useRef(initial) { return (slots[cursor++] ??= { current: initial }) },
    useCallback(fn, deps) { const i = cursor++; if (changed(slots[i]?.deps, deps)) slots[i] = { value: fn, deps }; return slots[i].value },
    useEffect(fn, deps) { const i = cursor++; if (changed(slots[i]?.deps, deps)) effects.push(() => { slots[i]?.cleanup?.(); slots[i] = { deps, cleanup: fn() } }) },
  }
  const document = { hidden: false, addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener: (type, fn) => { if (listeners.get(type) === fn) listeners.delete(type) } }
  const exports = {}
  vm.runInNewContext(compiled, { exports, require: () => react, document, AbortController,
    setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId }, clearTimeout: id => timers.delete(id) })
  function render() { cursor = 0; const view = exports.useMemoryWorkspace(api, visible); const pending = effects; effects = []; pending.forEach(fn => fn()); return view }
  render()
  return { render, timers,
    async flush() { let view; for (let i = 0; i < 4; i++) { await new Promise(resolve => setImmediate(resolve)); view = render() } return view },
    async poll() { const pending = [...timers.values()]; timers.clear(); pending.forEach(({ fn }) => fn()); return this.flush() },
    hideDocument(hidden) { document.hidden = hidden; listeners.get('visibilitychange')?.() },
    setVisible(value) { visible = value; return render() },
    close() { slots.forEach(slot => slot?.cleanup?.()) },
  }
}
function client() {
  const pages = { A: page('A'), B: page('B') }
  return { pages, writes: [], reads: 0,
    async listWikiPages(query, offset) { this.reads++; const items = Object.values(pages).filter(p => p.name.includes(query)); return { items: items.slice(offset, offset + 20), total: items.length, offset, limit: 20 } },
    async getWikiStatus() { return { pending: 0, repairs: [], extractions: [] } },
    async getWikiPage(name) { return { ...pages[name] } },
    async editWikiPage(name, draft) { this.writes.push(['edit', name, draft]); return (pages[name] = { ...pages[name], ...draft, revision: pages[name].revision + 1 }) },
    async deleteWikiPage(name, revision) { this.writes.push(['delete', name, revision]); delete pages[name]; return { deleted: true } },
    async repairWikiPage(name) { this.writes.push(['repair', name]); return pages[name] },
    async lintWiki() { this.writes.push(['lint']); return { issues: [], total_pages: 2 } },
  }
}

test('切换详情后旧请求不能覆盖新页面', async () => {
  const api = client(), old = deferred()
  api.getWikiPage = async name => name === 'A' ? old.promise : api.pages[name]
  const hook = workspace(api)
  let view = await hook.flush(); view.selectPage('B'); view = await hook.flush()
  assert.equal(view.detail.name, 'B')
  old.resolve(page('A')); view = await hook.flush()
  assert.equal(view.detail.name, 'B'); hook.close()
})

test('加载中重复选择当前记忆不会丢弃详情响应或卡住加载状态', async () => {
  const api = client(), pending = deferred()
  api.getWikiPage = async () => pending.promise
  const hook = workspace(api)
  let view = await hook.flush()
  assert.equal(view.selected, 'A'); assert.equal(view.loading, true)
  const reads = api.reads
  view.selectPage('A'); view.selectPage('A'); view = await hook.flush()
  pending.resolve(page('A')); view = await hook.flush()
  assert.equal(view.detail?.name, 'A'); assert.equal(view.loading, false)
  assert.equal(api.reads, reads); hook.close()
})

test('加载完成后重复选择当前记忆保留详情和未保存草稿', async () => {
  const api = client(), hook = workspace(api)
  let view = await hook.flush(); view.startEdit(); view = hook.render()
  view.updateDraft({ content: '尚未保存的草稿' }); view = hook.render()
  const detail = view.detail, draft = view.draft, reads = api.reads
  view.selectPage('A'); view = await hook.flush()
  assert.equal(view.detail, detail); assert.equal(view.draft, draft)
  assert.equal(view.loading, false); assert.equal(api.reads, reads)
  assert.equal(api.writes.length, 0); hook.close()
})

test('旧搜索不能覆盖新搜索和分页结果', async () => {
  const api = client(), old = deferred(), original = api.listWikiPages
  api.listWikiPages = async (query, offset) => query === 'old' ? old.promise : original.call(api, query, offset)
  const hook = workspace(api)
  let view = await hook.flush(); view.search('old'); await hook.flush()
  view = hook.render(); view.search('B'); view = await hook.flush()
  assert.equal(view.pages.items[0].name, 'B')
  old.resolve({ items: [page('旧页面')], total: 1, offset: 0, limit: 20 }); view = await hook.flush()
  assert.equal(view.pages.items[0].name, 'B'); hook.close()
})

test('分页查询并在删除最后一页后回到有效页，新搜索重置页码', async () => {
  const api = client()
  for (let i = 0; i < 20; i++) api.pages[`额外${i}`] = page(`额外${i}`)
  const hook = workspace(api)
  let view = await hook.flush(); view.setOffset(20); view = await hook.flush()
  assert.equal(view.pages.items.length, 2); assert.equal(view.offset, 20)
  Object.keys(api.pages).slice(20).forEach(name => delete api.pages[name])
  view.refresh(); view = await hook.flush()
  assert.equal(view.offset, 0); assert.equal(view.pages.items.length, 20)
  view.search('额外1'); view = await hook.flush()
  assert.equal(view.offset, 0); assert.ok(view.pages.items.every(item => item.name.includes('额外1')))
  hook.close()
})

test('版本冲突保留草稿及旧版本，刷新不会自动重发保存', async () => {
  const api = client()
  api.editWikiPage = async (name, draft) => { api.writes.push(['edit', name, draft]); throw Object.assign(new Error('版本冲突'), { status: 409 }) }
  const hook = workspace(api)
  let view = await hook.flush(); view.startEdit(); view = hook.render()
  view.updateDraft({ content: '我的草稿', tagsText: '中文, ' }); view = hook.render()
  assert.equal(view.draft.tagsText, '中文, ')
  api.pages.A = page('A', 2)
  await view.save(); view = await hook.flush()
  assert.equal(view.conflict, true); assert.equal(view.draft.content, '我的草稿')
  assert.equal(view.draft.revision, 1); assert.equal(view.detail.revision, 2)
  view.refresh(); view = await hook.flush()
  assert.equal(api.writes.length, 1); assert.equal(view.draft.content, '我的草稿'); hook.close()
})

test('不同记忆各自保留草稿，切换不会写入', async () => {
  const api = client(), hook = workspace(api)
  let view = await hook.flush(); view.startEdit(); view = hook.render()
  view.updateDraft({ content: 'A草稿' }); view = hook.render(); view.selectPage('B'); view = await hook.flush()
  view.startEdit(); view = hook.render(); view.updateDraft({ content: 'B草稿' }); view = hook.render()
  view.selectPage('A'); view = await hook.flush()
  assert.equal(view.draft.content, 'A草稿'); assert.equal(api.writes.length, 0); hook.close()
})

test('轮询每两秒执行，隐藏或离开停止，恢复只读取', async () => {
  const api = client(); api.getWikiStatus = async () => ({ pending: 1, repairs: [], extractions: [] })
  const hook = workspace(api); await hook.flush()
  assert.equal([...hook.timers.values()][0].delay, 2000)
  hook.hideDocument(true); assert.equal(hook.timers.size, 0)
  const reads = api.reads; await hook.poll(); assert.equal(api.reads, reads)
  hook.hideDocument(false); await hook.flush(); assert.equal(hook.timers.size, 1)
  hook.setVisible(false); assert.equal(hook.timers.size, 0); await hook.poll()
  hook.setVisible(true); await hook.flush(); assert.equal(api.writes.length, 0); hook.close()
})

test('重复删除只请求一次，晚到响应保留后来选中的页面', async () => {
  const api = client(), done = deferred()
  api.deleteWikiPage = async (name, revision) => { api.writes.push(['delete', name, revision]); await done.promise; delete api.pages[name]; return { deleted: true } }
  const hook = workspace(api); let view = await hook.flush()
  const first = view.remove(), second = view.remove(); view.selectPage('B'); view = await hook.flush()
  done.resolve(); await Promise.all([first, second]); view = await hook.flush()
  assert.equal(api.writes.length, 1); assert.equal(view.selected, 'B'); assert.equal(view.detail.name, 'B'); hook.close()
})

test('写入断线后只查询结果，不自动重试', async () => {
  const api = client(); api.repairWikiPage = async name => { api.writes.push(['repair', name]); throw new Error('连接断开') }
  const hook = workspace(api); let view = await hook.flush()
  await view.repair(); view = await hook.flush(); assert.match(view.error, /连接断开/)
  view.refresh(); await hook.flush(); assert.equal(api.writes.length, 1); hook.close()
})
