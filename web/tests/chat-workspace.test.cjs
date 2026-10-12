const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')

const compiled = ts.transpileModule(readFileSync('src/features/chat/useChatWorkspace.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const session = id => ({ id, title: id, history_version: 0, message_count: 0, updated_at: '', messages: [], events: [], total_tokens: 0, run_status: 'idle', run_error: null })
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
    renameSession: async (id, title) => { sessions[id] = { ...sessions[id], title }; return sessions[id] },
    deleteSession: async id => { delete sessions[id] },
    deleteLatestUserMessage: async (id, messageId) => {
      const current = sessions[id]
      const index = current.messages.findLastIndex(message => message.uuid === messageId)
      sessions[id] = { ...current, history_version: current.history_version + 1, messages: current.messages.slice(0, index), events: [] }
      return sessions[id]
    },
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

test('子 Agent 卡片按父会话消息排序，点击只切换右侧详情', async () => {
  const api = client()
  api.sessions.A.messages = [{ role: 'user', content: '分配任务' }, { role: 'assistant', content: '我来汇总' }]
  api.sessions.A.events = [
    { kind: 'subagent', content: '任务一', meta: { child_session_id: 'sub-worker-1', agent_id: 'worker', state: 'running', after_message_index: 0 } },
    { kind: 'subagent', content: '任务二', meta: { child_session_id: 'sub-worker-2', agent_id: 'worker', state: 'running', after_message_index: 0 } },
    { kind: 'subagent', meta: { child_session_id: 'sub-worker-1', state: 'completed' } },
    { kind: 'subagent', meta: { child_session_id: 'sub-worker-2', state: 'failed' } },
  ]
  api.sessions['sub-worker-1'] = { ...session('sub-worker-1'), messages: [
    { role: 'user', content: '任务一' }, { role: 'assistant', content: '已检查' }, { role: 'tool', content: '文件正文' },
  ] }
  api.sessions['sub-worker-2'] = session('sub-worker-2')
  api.listSessions = async () => [api.sessions.A, api.sessions.B]
  const hook = workspace(api)
  let view = await hook.flush()
  assert.deepEqual(Array.from(view.timeline, item => 'childSessionId' in item ? item.task : item.content),
    ['分配任务', '任务一', '任务二', '我来汇总'])
  assert.deepEqual(Array.from(view.cards, card => card.state), ['completed', 'failed'])
  view.selectSubAgent('sub-worker-1')
  view = await hook.flush()
  assert.equal(view.activeId, 'A')
  assert.equal(view.selectedCard.task, '任务一')
  assert.deepEqual(Array.from(view.childMessages, message => message.content), ['任务一', '已检查', '文件正文'])
  await view.loadConversation('B')
  view = hook.render()
  assert.equal(view.selectedCard, null)
  await view.loadConversation('A')
  view = hook.render()
  assert.equal(view.selectedCard.task, '任务一')
  hook.close()
})

test('子任务流式文字和消息保存实时更新详情，不重复提交任务', async () => {
  const api = client(), childStream = deferred(), parentStream = deferred()
  let childEvents, liveText, sends = 0
  api.sessions.A.messages = [{ role: 'user', content: '启动子任务' }]
  api.sessions.A.events = [{ kind: 'subagent', content: '查文件', meta: {
    child_session_id: 'sub-worker-1', agent_id: 'worker', state: 'running', after_message_index: 0,
  } }]
  api.sessions['sub-worker-1'] = { ...session('sub-worker-1'), run_status: 'running', messages: [{ role: 'user', content: '查文件' }] }
  api.listSessions = async () => [api.sessions.A, api.sessions.B]
  api.chatStream = async () => { sends++ }
  api.streamEvents = async (id, _after, onEvent, _signal, onLiveText) => {
    if (id === 'sub-worker-1') { childEvents = onEvent; liveText = onLiveText; return childStream.promise }
    return parentStream.promise
  }
  const hook = workspace(api)
  let view = await hook.flush()
  view.selectSubAgent('sub-worker-1')
  view = await hook.flush()
  liveText('正在读')
  view = hook.render()
  assert.equal(view.childStreaming, '正在读')
  assert.equal(view.activeId, 'A')
  api.sessions['sub-worker-1'] = { ...api.sessions['sub-worker-1'], messages: [
    { role: 'user', content: '查文件' }, { role: 'assistant', content: '读完了' },
  ] }
  childEvents(0, { kind: 'message_saved', title: '消息已保存' })
  view = await hook.flush()
  assert.equal(view.childMessages.at(-1).content, '读完了')
  assert.equal(sends, 0)
  childStream.resolve()
  parentStream.resolve()
  hook.close()
})

test('刷新后未完成但服务端已停止的子任务显示中断', async () => {
  const api = client()
  api.sessions.A.events = [{ kind: 'subagent', content: '检查', meta: {
    child_session_id: 'sub-worker-1', agent_id: 'worker', state: 'running', after_message_index: -1,
  } }]
  api.sessions['sub-worker-1'] = session('sub-worker-1')
  api.listSessions = async () => [api.sessions.A, api.sessions.B]
  const hook = workspace(api)
  const view = await hook.flush()
  assert.equal(view.cards[0].state, 'interrupted')
  hook.close()
})

test('主 Agent 已保存的中间回复不与后续流式文字重复', async () => {
  const api = client(), pending = deferred(), events = deferred()
  let sendDelta, onEvent
  api.chatStream = (_id, _content, onDelta, onReady) => {
    sendDelta = onDelta
    onReady()
    onDelta('先查')
    return pending.promise
  }
  api.streamEvents = (_id, _after, callback) => { onEvent = callback; return events.promise }
  const hook = workspace(api)
  let view = await hook.flush()
  const sending = view.send('任务')
  view = hook.render()
  assert.equal(view.streaming, '先查')
  api.sessions.A = { ...api.sessions.A, run_status: 'running', messages: [
    { role: 'user', content: '任务' }, { role: 'assistant', content: '先查' },
  ] }
  onEvent(0, { kind: 'message_saved', title: '消息已保存' })
  view = await hook.flush()
  assert.equal(view.streaming, '')
  assert.equal(view.messages.at(-1).content, '先查')
  sendDelta('最终')
  view = hook.render()
  assert.equal(view.streaming, '最终')
  pending.resolve()
  events.resolve()
  await sending
  hook.close()
})

test('父会话过程流断线后只重连读取，不重发聊天任务', async () => {
  const api = client(), second = deferred()
  api.sessions.A.active_subagents = true
  api.sessions.A.events = [{ kind: 'subagent', content: '查文件', meta: {
    child_session_id: 'sub-worker-1', agent_id: 'worker', state: 'running', after_message_index: -1,
  } }]
  api.sessions['sub-worker-1'] = { ...session('sub-worker-1'), run_status: 'running' }
  api.listSessions = async () => [api.sessions.A, api.sessions.B]
  let reads = 0, sends = 0
  api.streamEvents = async () => ++reads === 1 ? Promise.reject(new Error('断线')) : second.promise
  api.chatStream = async () => { sends++ }
  const hook = workspace(api)
  await hook.flush()
  assert.equal(reads, 1)
  await hook.poll()
  assert.equal(reads, 2)
  assert.equal(sends, 0)
  second.resolve()
  await hook.flush()
  hook.close()
})

test('空会话改名后列表保留自定义标题，删除当前会话切换到下一条', async () => {
  const api = client(), hook = workspace(api)
  let view = await hook.flush()
  await view.renameConversation('A', '自定义标题')
  view = hook.render()
  assert.equal(view.conversations.find(item => item.id === 'A').title, '自定义标题')
  await view.deleteConversation('A')
  view = hook.render()
  assert.equal(view.activeId, 'B')
  assert.equal(view.conversations.some(item => item.id === 'A'), false)
  assert.equal(view.session.id, 'B')
  hook.close()
})

test('编辑最后一条用户消息清除旧回复，并挡住迟到的旧会话读取', async () => {
  const api = client(), pending = deferred()
  api.sessions.A.messages = [
    { role: 'user', uuid: 'first', content: '第一轮', turn_event_index: 0 },
    { role: 'assistant', content: '第一轮回复' },
    { role: 'user', uuid: 'last', content: '旧问题', turn_event_index: 1 },
    { role: 'assistant', content: '旧回复' },
  ]
  api.sessions.A.events = [{ kind: 'status', title: '执行中', content: '第一轮' }, { kind: 'status', title: '执行中', content: '旧过程' }]
  let calls = 0, editId
  api.chatStream = (_id, content, onDelta, onReady, id) => {
    calls++
    editId = id
    onReady()
    onDelta('新片段')
    return pending.promise
  }
  const hook = workspace(api)
  let view = await hook.flush()
  const sending = view.editLatestMessage('last', '新问题')
  view = hook.render()
  assert.deepEqual(Array.from(view.messages, message => message.content), ['第一轮', '第一轮回复', '新问题'])
  assert.deepEqual(Array.from(view.session.events, event => event.content), ['第一轮'])
  assert.equal(view.streaming, '新片段')
  assert.equal(editId, 'last')
  api.sessions.A.run_status = 'running'
  view = await hook.poll()
  assert.equal(view.messages.at(-1).content, '新问题')
  assert.equal(view.session.events.length, 1)
  api.sessions.A = { ...api.sessions.A, history_version: 1, run_status: 'idle', messages: [
    ...api.sessions.A.messages.slice(0, 2), { role: 'user', uuid: 'last', content: '新问题', turn_event_index: 1 },
    { role: 'assistant', content: '新回复' },
  ], events: [{ kind: 'status', title: '执行中', content: '第一轮' }] }
  pending.resolve()
  await sending
  view = hook.render()
  assert.equal(view.messages.at(-1).content, '新回复')
  assert.equal(view.streaming, '')
  assert.equal(calls, 1)
  hook.close()
})

test('删除末轮后旧读取不能恢复被删回复，也不会重跑 Agent', async () => {
  const api = client(), pending = deferred()
  api.sessions.A.messages = [
    { role: 'user', uuid: 'first', content: '第一轮', turn_event_index: 0 },
    { role: 'assistant', content: '保留' },
    { role: 'user', uuid: 'last', content: '删除我', turn_event_index: 1 },
    { role: 'assistant', content: '旧回复' },
  ]
  const old = { ...api.sessions.A, messages: [...api.sessions.A.messages] }
  let sends = 0
  api.chatStream = async () => { sends++ }
  const hook = workspace(api)
  let view = await hook.flush()
  api.getSession = id => id === 'A' ? pending.promise : Promise.resolve(api.sessions[id])
  const lateRead = view.loadConversation('A')
  await view.deleteLatestMessage('last')
  pending.resolve(old)
  await lateRead
  view = hook.render()
  assert.deepEqual(Array.from(view.messages, message => message.content), ['第一轮', '保留'])
  assert.equal(view.session.history_version, 1)
  assert.equal(sends, 0)
  hook.close()
})

test('编辑旧会话时保留前一轮的过程事件', async () => {
  const api = client(), pending = deferred()
  api.sessions.A.messages = [
    { role: 'user', uuid: 'first', content: '第一轮' },
    { role: 'assistant', content: '完成' },
    { role: 'user', uuid: 'last', content: '第二轮' },
  ]
  api.sessions.A.events = [
    { kind: 'status', content: '第一轮过程' }, { kind: 'message_saved', title: '消息已保存' },
    { kind: 'status', content: '第二轮过程' },
  ]
  api.chatStream = () => pending.promise
  const hook = workspace(api)
  const view = await hook.flush()
  const editing = view.editLatestMessage('last', '修改后')
  assert.deepEqual(Array.from(hook.render().session.events, event => event.kind), ['status', 'message_saved'])
  pending.resolve()
  await editing
  hook.close()
})

for (const failedRead of [false, true]) {
  test(`编辑被 409 拒绝后恢复原消息，并能继续查询到任务完成（状态读取失败：${failedRead}）`, async () => {
    const api = client()
    const original = [{ role: 'user', uuid: 'last', content: '原问题', turn_event_index: 0 }, { role: 'assistant', content: '原回复' }]
    api.sessions.A = { ...api.sessions.A, messages: original, events: [{ kind: 'message_saved', title: '消息已保存' }] }
    let sends = 0
    const hook = workspace(api)
    let view = await hook.flush()
    api.chatStream = async () => {
      sends++
      api.sessions.A = { ...api.sessions.A, run_status: 'running' }
      if (failedRead) {
        let first = true
        api.getSession = async id => {
          if (first) { first = false; throw new Error('状态读取断线') }
          return api.sessions[id]
        }
      }
      throw new Error('409：会话正在执行')
    }
    await view.editLatestMessage('last', '被拒绝的修改')
    view = await hook.flush()
    assert.equal(view.session.history_version, 0)
    assert.deepEqual(Array.from(view.messages, message => message.content), ['原问题', '原回复'])
    assert.equal(view.session.events.length, 1)
    assert.equal(view.draft, '被拒绝的修改')
    api.sessions.A = { ...api.sessions.A, run_status: 'idle', messages: [...original, { role: 'assistant', content: '其他页面任务完成' }] }
    view = await hook.poll()
    assert.equal(view.running, false)
    assert.equal(view.messages.at(-1).content, '其他页面任务完成')
    assert.equal(sends, 1)
    hook.close()
  })
}

for (const operation of ['deleteConversation', 'deleteLatestMessage']) {
  test(`${operation} 等待期间阻止当前会话写入，其他会话仍能发送`, async () => {
    const api = client(), pending = deferred()
    api.sessions.A.messages = [{ role: 'user', uuid: 'last', content: '原问题', turn_event_index: 0 }]
    let deletes = 0, messageDeletes = 0, renames = 0
    const sends = []
    api.deleteSession = async id => { deletes++; await pending.promise; delete api.sessions[id] }
    api.deleteLatestUserMessage = async id => {
      messageDeletes++
      await pending.promise
      api.sessions[id] = { ...api.sessions[id], history_version: 1, messages: [], events: [] }
      return api.sessions[id]
    }
    api.renameSession = async id => { renames++; return api.sessions[id] }
    api.chatStream = async id => { sends.push(id) }
    const hook = workspace(api)
    let view = await hook.flush()
    const deleting = operation === 'deleteConversation' ? view.deleteConversation('A') : view.deleteLatestMessage('last')
    assert.equal(hook.render().mutating, true)
    await view.send('不能发送')
    await view.editLatestMessage('last', '不能编辑')
    await view.deleteConversation('A')
    await view.deleteLatestMessage('last')
    await view.renameConversation('A', '不能改名')
    assert.deepEqual(sends, [])
    assert.equal(deletes + messageDeletes, 1)
    assert.equal(renames, 0)
    await view.loadConversation('B')
    view = hook.render()
    assert.equal(view.mutating, false)
    await view.send('其他会话可以发送')
    assert.deepEqual(sends, ['B'])
    pending.resolve()
    await deleting
    assert.equal(hook.render().mutatingIds.size, 0)
    hook.close()
  })

  test(`${operation} 失败后释放操作锁，并保留消息与草稿`, async () => {
    const api = client(), pending = deferred()
    api.sessions.A.messages = [{ role: 'user', uuid: 'last', content: '原问题', turn_event_index: 0 }]
    api.deleteSession = () => pending.promise
    api.deleteLatestUserMessage = () => pending.promise
    let sends = 0
    api.chatStream = async () => { sends++ }
    const hook = workspace(api)
    let view = await hook.flush()
    view.setDraft('保留草稿')
    view = hook.render()
    const deleting = operation === 'deleteConversation' ? view.deleteConversation('A') : view.deleteLatestMessage('last')
    pending.reject(new Error('删除失败'))
    await deleting
    view = hook.render()
    assert.equal(view.mutating, false)
    assert.equal(view.messages[0].content, '原问题')
    assert.equal(view.draft, '保留草稿')
    assert.match(view.error, /删除失败/)
    await view.send('失败后可以发送')
    assert.equal(sends, 1)
    hook.close()
  })
}

test('删除响应晚到时保留后来选中的会话', async () => {
  const api = client(), pending = deferred()
  api.sessions.C = session('C')
  api.deleteSession = async id => { await pending.promise; delete api.sessions[id] }
  const hook = workspace(api)
  const view = await hook.flush()
  const deleting = view.deleteConversation('A')
  await view.loadConversation('C')
  pending.resolve()
  await deleting
  const current = hook.render()
  assert.equal(current.activeId, 'C')
  assert.equal(current.session.id, 'C')
  hook.close()
})

test('旧会话保存事件不完整时，编辑立即清理旧过程和子任务卡片', async () => {
  const api = client(), pending = deferred()
  api.sessions.A.messages = [
    { role: 'user', uuid: 'first', content: '第一轮' }, { role: 'assistant', content: '没有保存事件的旧回复' },
    { role: 'user', uuid: 'last', content: '第二轮' }, { role: 'assistant', content: '有保存事件的新回复' },
  ]
  api.sessions.A.events = [
    { kind: 'subagent', content: '第二轮子任务', meta: { child_session_id: 'sub-old', state: 'running', after_message_index: 2 } },
    { kind: 'subagent', meta: { child_session_id: 'sub-old', state: 'completed' } },
    { kind: 'message_saved', title: '消息已保存' },
  ]
  api.chatStream = () => pending.promise
  const hook = workspace(api)
  const view = await hook.flush()
  const editing = view.editLatestMessage('last', '修改后')
  const current = hook.render()
  assert.equal(current.session.events.length, 0)
  assert.equal(current.cards.length, 0)
  assert.deepEqual(Array.from(current.messages, message => message.content), ['第一轮', '没有保存事件的旧回复', '修改后'])
  api.sessions.A = { ...api.sessions.A, history_version: 1, events: [], messages: current.session.messages }
  pending.resolve()
  await editing
  hook.close()
})
