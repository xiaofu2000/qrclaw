const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')

const compiled = ts.transpileModule(readFileSync('src/features/chat/useChatWorkspace.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const session = id => ({ id, title: id, message_count: 0, updated_at: '', messages: [], events: [], total_tokens: 0, run_status: 'idle', run_error: null })
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

// Run the real hook with deterministic effects and manually controlled network completion.
function workspace(api) {
  const slots = []
  const timers = new Map()
  let timerId = 0
  let cursor = 0, effects = [], current
  const changed = (before, after) => !before || after.some((value, index) => value !== before[index])
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
    useEffect(fn, deps) {
      const index = cursor++
      if (changed(slots[index]?.deps, deps)) effects.push(() => {
        slots[index]?.cleanup?.()
        slots[index] = { deps, cleanup: fn() }
      })
    },
  }
  const exports = {}
  vm.runInNewContext(compiled, {
    exports, require: () => react,
    setTimeout: fn => { timers.set(++timerId, fn); return timerId },
    clearTimeout: id => timers.delete(id),
    AbortController,
  })
  function render() {
    cursor = 0
    current = exports.useChatWorkspace(api)
    const pending = effects
    effects = []
    pending.forEach(effect => effect())
    return current
  }
  render()
  return {
    render,
    async flush() {
      await new Promise(resolve => setImmediate(resolve))
      render()
      await new Promise(resolve => setImmediate(resolve))
      return render()
    },
    async poll() {
      const pending = [...timers.values()]
      timers.clear()
      pending.forEach(fn => fn())
      return this.flush()
    },
    close() { slots.forEach(slot => slot?.cleanup?.()) },
  }
}
function client() {
  const sessions = { A: session('A'), B: session('B') }
  return {
    sessions,
    health: async () => ({}),
    listSessions: async () => Object.values(sessions),
    getSession: async id => sessions[id],
    createSession: async () => sessions.B,
    chatStream: async () => '',
    streamEvents: async () => {},
  }
}

test('A completing cannot clear B running state or B draft', async () => {
  const api = client(), a = deferred(), b = deferred()
  api.chatStream = id => (id === 'A' ? a : b).promise
  const hook = workspace(api)
  let view = await hook.flush()
  const sendingA = view.send('task A')
  view = hook.render()
  view.setDraft('next A')
  await view.newConversation()
  view = hook.render()
  assert.equal(view.activeId, 'B')
  assert.equal(view.running, false)
  const sendingB = view.send('task B')
  view = hook.render()
  view.setDraft('next B')
  a.resolve('done A')
  await sendingA
  view = await hook.flush()
  assert.equal(view.activeId, 'B')
  assert.equal(view.running, true)
  assert.equal(view.draft, 'next B')
  assert.equal(view.runningIds.has('A'), false)
  await view.loadConversation('A')
  view = hook.render()
  assert.equal(view.running, false)
  assert.equal(view.draft, 'next A')
  b.resolve('done B')
  await sendingB
  hook.close()
})

test('rapid sends in one session execute once', async () => {
  const api = client(), pending = deferred()
  let calls = 0
  api.chatStream = () => { calls++; return pending.promise }
  const hook = workspace(api), view = await hook.flush()
  const first = view.send('hello')
  await view.send('duplicate')
  assert.equal(calls, 1)
  pending.resolve('ok')
  await first
  hook.close()
})

test('failed request preserves a newer draft and allows retry', async () => {
  const api = client(), pending = deferred()
  api.chatStream = () => pending.promise
  const hook = workspace(api)
  let view = await hook.flush()
  const sending = view.send('old draft')
  view = hook.render()
  view.setDraft('new draft')
  pending.reject(new Error('offline'))
  await sending
  view = hook.render()
  assert.equal(view.draft, 'new draft')
  assert.equal(view.running, false)
  assert.match(view.error, /offline/)
  hook.close()
})

test('completed task with failed result read refreshes without resending', async () => {
  const api = client()
  let calls = 0
  const hook = workspace(api)
  let view = await hook.flush()
  api.chatStream = async () => { calls++; api.getSession = async () => { throw new Error('read failed') } }
  await view.send('execute once')
  view = hook.render()
  assert.match(view.error, /任务已完成/)
  assert.equal(view.draft, '')
  api.getSession = async id => api.sessions[id]
  await view.refresh()
  view = hook.render()
  assert.equal(view.error, '')
  assert.equal(calls, 1)
  hook.close()
})

test('late session load updates its own cache without switching selection', async () => {
  const api = client(), pending = deferred(), hook = workspace(api)
  let view = await hook.flush()
  api.getSession = id => id === 'A' ? pending.promise : Promise.resolve(api.sessions[id])
  const loadingA = view.loadConversation('A')
  await view.loadConversation('B')
  pending.resolve(api.sessions.A)
  await loadingA
  view = hook.render()
  assert.equal(view.activeId, 'B')
  assert.equal(view.session.id, 'B')
  assert.equal(view.loading, false)
  hook.close()
})

test('reload restores server running tasks and polling retrieves completion', async () => {
  const api = client()
  api.sessions.A.run_status = 'running'
  api.sessions.B.run_status = 'queued'
  let calls = 0
  api.chatStream = async () => { calls++ }
  const hook = workspace(api)
  let view = await hook.flush()
  assert.equal(view.running, true)
  await view.send('must not repeat')
  assert.equal(calls, 0)
  await view.loadConversation('B')
  view = await hook.flush()
  assert.equal(view.activeId, 'B')
  assert.equal(view.running, true)
  api.sessions.A = { ...api.sessions.A, run_status: 'idle', messages: [{ role: 'assistant', content: 'A done' }] }
  api.sessions.B = { ...api.sessions.B, run_status: 'idle', messages: [{ role: 'assistant', content: 'B done' }] }
  view = await hook.poll()
  assert.equal(view.running, false)
  assert.equal(view.runningIds.size, 0)
  assert.equal(view.messages[0].content, 'B done')
  assert.equal(calls, 0)
  hook.close()
})

test('bootstrap cannot overwrite completion with an older session response', async () => {
  const api = client(), old = deferred()
  let listings = 0
  api.listSessions = async () => [{ ...api.sessions.A, run_status: listings++ ? 'idle' : 'running' }]
  let reads = 0
  api.getSession = () => ++reads === 1 ? old.promise : Promise.resolve({
    ...api.sessions.A, messages: [{ role: 'assistant', content: 'final result' }],
  })
  const hook = workspace(api)
  await hook.flush()
  old.resolve({ ...api.sessions.A, run_status: 'running' })
  const view = await hook.flush()
  assert.equal(view.running, false)
  assert.equal(view.messages[0]?.content, 'final result')
  hook.close()
})

test('流式正文按会话隔离，过程事件按序号去重', async () => {
  const api = client(), a = deferred(), b = deferred()
  api.chatStream = (id, _content, onDelta, onReady) => {
    onReady()
    onDelta(id === 'A' ? '甲' : '乙')
    return (id === 'A' ? a : b).promise
  }
  api.streamEvents = async (_id, _after, onEvent) => {
    const event = { kind: 'tool_status', title: '工具开始', content: 'read_file' }
    onEvent(0, event)
    onEvent(0, event)
  }
  const hook = workspace(api)
  let view = await hook.flush()
  const sendingA = view.send('任务 A')
  view = await hook.flush()
  assert.equal(view.streaming, '甲')
  assert.equal(view.session.events.length, 1)
  await view.newConversation()
  view = hook.render()
  const sendingB = view.send('任务 B')
  view = await hook.flush()
  assert.equal(view.streaming, '乙')
  await view.loadConversation('A')
  view = hook.render()
  assert.equal(view.streaming, '甲')
  a.resolve()
  b.resolve()
  await Promise.all([sendingA, sendingB])
  hook.close()
})

test('流式连接断开后不重发，轮询接回后台完成结果', async () => {
  const api = client()
  let calls = 0
  api.chatStream = async (_id, _content, onDelta, onReady) => {
    calls++
    api.sessions.A.run_status = 'running'
    onReady()
    onDelta('部分')
    throw new Error('连接中断')
  }
  const hook = workspace(api)
  let view = await hook.flush()
  await view.send('只执行一次')
  view = hook.render()
  assert.equal(calls, 1)
  assert.equal(view.running, true)
  assert.equal(view.streaming, '部分')
  api.sessions.A = { ...api.sessions.A, run_status: 'idle', messages: [
    { role: 'user', content: '只执行一次' }, { role: 'assistant', content: '最终结果' },
  ] }
  view = await hook.poll()
  if (view.running) view = await hook.poll()
  assert.equal(view.running, false)
  assert.equal(view.streaming, '')
  assert.equal(view.messages.at(-1).content, '最终结果')
  assert.equal(view.error, '')
  assert.equal(calls, 1)
  hook.close()
})

test('后端返回同会话冲突时移除未提交的乐观消息', async () => {
  const api = client()
  api.chatStream = async () => {
    api.sessions.A.run_status = 'running'
    throw new Error('会话已有任务正在执行')
  }
  const hook = workspace(api)
  const view = await hook.flush()
  await view.send('重复请求')
  const latest = hook.render()
  assert.equal(latest.messages.length, 0)
  assert.equal(latest.draft, '重复请求')
  hook.close()
})
