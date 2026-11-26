const { test } = require('node:test')
const assert = require('node:assert/strict')
const { loadModule, hookRuntime, collectNodes, textOf, deferred } = require('./helpers/load.cjs')

function renderComposer(overrides = {}) {
  const sent = []
  let stopped = 0
  const exports = loadModule('src/features/composer/Composer.tsx', {
    mocks: {
      react: { useRef: () => ({ current: null }), useLayoutEffect: () => {} },
    },
  })
  const tree = exports.Composer({
    value: '下一条草稿',
    onChange: () => {},
    onSend: content => sent.push(content),
    disabled: false,
    loading: false,
    running: false,
    planEnabled: false,
    onTogglePlan: () => {},
    contextUsage: null,
    compressionNotice: null,
    ...overrides,
    ...(overrides.onStop === true ? { onStop: () => { stopped += 1 } } : {}),
  })
  const nodes = collectNodes(tree)
  return {
    tree,
    sent,
    getStopped: () => stopped,
    stopButton: nodes.find(node => node.props?.className === 'stop-button'),
    sendButton: nodes.find(node => node.props?.type === 'submit'),
    hint: nodes.find(node => node.props?.className === 'composer-hint'),
  }
}

function makeSession(id) {
  return {
    id,
    title: id,
    history_version: 0,
    message_count: 0,
    updated_at: '',
    messages: [],
    events: [],
    total_tokens: 0,
    run_status: 'idle',
    active_subagents: false,
    run_error: null,
  }
}

function makeApiClient() {
  const sessions = { A: makeSession('A'), B: makeSession('B') }
  const cancelledIds = []
  return {
    sessions,
    cancelledIds,
    health: async () => ({ status: 'ok' }),
    listSessions: async () => Object.values(sessions),
    getSession: async id => structuredClone(sessions[id]),
    createSession: async () => sessions.B,
    renameSession: async (id, title) => {
      sessions[id] = { ...sessions[id], title }
      return structuredClone(sessions[id])
    },
    deleteSession: async id => {
      delete sessions[id]
    },
    deleteLatestUserMessage: async (id, messageId) => {
      const current = sessions[id]
      const index = current.messages.findLastIndex(message => message.uuid === messageId)
      sessions[id] = {
        ...current,
        history_version: current.history_version + 1,
        messages: current.messages.slice(0, index),
        events: [],
      }
      return structuredClone(sessions[id])
    },
    cancelSession: async id => {
      cancelledIds.push(id)
      sessions[id] = { ...sessions[id], run_status: 'idle', active_subagents: false, run_error: null }
      return { cancelled: true, session_id: id, run_status: 'idle' }
    },
    chatStream: async () => '',
    streamEvents: async () => {},
  }
}

function mountWorkspace(api) {
  const runtime = hookRuntime()
  const timers = new Map()
  let timerId = 0
  const { useChatWorkspace } = loadModule('src/features/chat/useChatWorkspace.ts', {
    mocks: { react: runtime.react },
    globals: {
      setTimeout: fn => { timers.set(++timerId, fn); return timerId },
      clearTimeout: id => timers.delete(id),
      AbortController,
      localStorage: { getItem: () => null, setItem: () => {} },
    },
  })
  const render = () => runtime.render(() => useChatWorkspace(api))
  render()
  return {
    render,
    async flush() {
      await new Promise(resolve => setImmediate(resolve))
      render()
      await new Promise(resolve => setImmediate(resolve))
      return render()
    },
    close: runtime.close,
  }
}

test('Composer 在运行中渲染醒目停止按钮，支持停止中禁用态，且不破坏原有提交禁用契约', () => {
  // 空闲态不显示停止按钮
  const idle = renderComposer({ onStop: true, running: false })
  assert.equal(idle.stopButton, undefined)
  assert.equal(idle.sendButton.props.disabled, false)

  // 运行态但未传 onStop 时不显示停止按钮
  const legacyRunning = renderComposer({ running: true, disabled: true })
  assert.equal(legacyRunning.stopButton, undefined)
  assert.equal(legacyRunning.sendButton.props.disabled, true)

  // 运行态传入 onStop 时显示停止按钮，点击触发回调且不提交表单
  const active = renderComposer({ running: true, disabled: true, onStop: true })
  assert.ok(active.stopButton)
  assert.equal(active.stopButton.props.type, 'button')
  assert.equal(active.stopButton.props.disabled, false)
  assert.equal(active.stopButton.props['aria-label'], '停止任务')
  assert.equal(active.sendButton.props.disabled, true)
  assert.match(textOf(active.hint), /随时停止/)

  active.stopButton.props.onClick()
  assert.equal(active.getStopped(), 1)
  assert.deepEqual(active.sent, [])

  // stopping=true 时禁用停止按钮并切换无障碍文案
  const stopping = renderComposer({ running: true, disabled: true, onStop: true, stopping: true })
  assert.equal(stopping.stopButton.props.disabled, true)
  assert.equal(stopping.stopButton.props['aria-label'], '正在停止')
})

test('createApiClient.cancelSession 对会话 ID 编码并发送 POST /cancel 请求', async () => {
  const requests = []
  const { createApiClient } = loadModule('src/lib/api.ts', {
    transform: source => source.replaceAll('import.meta.env', '({})'),
    globals: {
      fetch: async (url, init) => {
        requests.push({ url, init })
        return {
          ok: true,
          json: async () => ({ cancelled: true, session_id: 'a/b', run_status: 'idle' }),
        }
      },
    },
  })
  const api = createApiClient()
  const res = await api.cancelSession('a/b')
  assert.deepEqual(res, { cancelled: true, session_id: 'a/b', run_status: 'idle' })
  assert.equal(requests.length, 1)
  assert.equal(requests[0].url, '/v1/sessions/a%2Fb/cancel')
  assert.equal(requests[0].init.method, 'POST')
})

test('useChatWorkspace.stop() 收敛流式与子任务状态，不报红错误，并立即允许编辑、删消息和删会话', async () => {
  const api = makeApiClient()
  const streamPending = deferred()
  const cancelGate = deferred()
  const eventsPending = deferred()

  let emitDelta
  api.chatStream = (_id, _content, onDelta, onReady) => {
    emitDelta = onDelta
    onReady()
    return streamPending.promise
  }
  api.streamEvents = () => eventsPending.promise
  api.cancelSession = async id => {
    api.cancelledIds.push(id)
    await cancelGate.promise
    api.sessions[id] = {
      ...api.sessions[id],
      run_status: 'idle',
      active_subagents: false,
      run_error: null,
      message_count: 2,
      messages: [
        { role: 'user', uuid: 'u-1', content: '执行长任务', turn_event_index: 0 },
        { role: 'assistant', uuid: 'a-1', content: '半截输出' },
      ],
      events: [
        { kind: 'subagent', title: '子 Agent 启动', content: '调研', meta: { child_session_id: 'sub-1', agent_id: 'worker', state: 'running', after_message_index: 0 } },
        { kind: 'subagent', title: '子 Agent 已停止', meta: { child_session_id: 'sub-1', state: 'interrupted' } },
        { kind: 'status', title: '任务已停止', content: '任务已由用户停止', meta: { code: 'run_cancelled' } },
      ],
    }
    return { cancelled: true, session_id: id, run_status: 'idle' }
  }

  const hook = mountWorkspace(api)
  let view = await hook.flush()

  // 1. 启动流式聊天并产生增量文本
  const sendPromise = view.send('执行长任务')
  emitDelta('半截输出')
  view = hook.render()
  assert.equal(view.running, true)
  assert.equal(view.streaming, '半截输出')
  assert.equal(view.cancelling, false)

  // 2. 点击停止：在后端响应前处于 cancelling 状态
  const stopPromise = view.stop()
  view = hook.render()
  assert.equal(view.cancelling, true)
  assert.deepEqual(api.cancelledIds, ['A'])

  // 3. 后端完成取消，即使底层流随后断开报错，界面也收敛为 ready 且无错误横幅
  cancelGate.resolve()
  await stopPromise
  streamPending.reject(new Error('stream aborted'))
  eventsPending.resolve()
  await sendPromise
  view = await hook.flush()

  assert.equal(view.cancelling, false)
  assert.equal(view.running, false)
  assert.equal(view.streaming, '')
  assert.equal(view.error, '')
  assert.equal(view.status, 'ready')
  assert.equal(view.cards.length, 1)
  assert.equal(view.cards[0].state, 'interrupted')

  // 4. 取消后可立即编辑最后一条用户消息重跑
  const editedCalls = []
  api.chatStream = async (id, content, _onDelta, onReady, editMessageId) => {
    editedCalls.push([id, content, editMessageId])
    onReady?.()
    api.sessions[id] = {
      ...api.sessions[id],
      history_version: 1,
      run_status: 'idle',
      message_count: 2,
      messages: [
        { role: 'user', uuid: 'u-1', content, turn_event_index: 0 },
        { role: 'assistant', uuid: 'a-2', content: '重跑完成' },
      ],
      events: [],
    }
  }
  await view.editLatestMessage('u-1', '修改后的新问题')
  view = await hook.flush()
  assert.deepEqual(editedCalls, [['A', '修改后的新问题', 'u-1']])
  assert.equal(view.messages.at(-1).content, '重跑完成')

  // 5. 可立即删除最后一条用户消息，以及删除整个会话
  await view.deleteLatestMessage('u-1')
  view = await hook.flush()
  assert.equal(view.messages.length, 0)

  await view.deleteConversation('A')
  view = await hook.flush()
  assert.equal(view.activeId, 'B')
  assert.equal('A' in api.sessions, false)

  hook.close()
})
