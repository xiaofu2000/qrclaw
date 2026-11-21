const { test } = require('node:test')
const assert = require('node:assert/strict')
const { loadModule } = require('./helpers/load.cjs')

/** 沙箱里创建的数组和对象原型不同，比较前转成普通数据。 */
const plain = value => JSON.parse(JSON.stringify(value))

function model(storage = new Map()) {
  return loadModule('src/features/chat/chatModel.ts', { globals: {
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
  } })
}

const detail = (patch = {}) => ({ id: 'A', title: 'A', history_version: 0, message_count: 0, updated_at: '', run_status: 'idle', run_error: null,
  prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, messages: [], events: [], ...patch })

test('增量读取拼在同一历史版本的缓存后面，版本或长度对不上时要求完整读取', () => {
  const { mergeSessionDelta, serverCounts, sessionCursor } = model()
  const cached = detail({ messages: [{ role: 'user', content: '问' }, { role: 'assistant', content: '答' }], events: [{ kind: 'a' }, { kind: 'b' }] })
  const delta = detail({ messages_offset: 2, events_offset: 2, event_count: 3, message_count: 3,
    messages: [{ role: 'assistant', content: '新' }], events: [{ kind: 'c' }] })
  const merged = mergeSessionDelta(cached, delta)
  assert.deepEqual(plain(merged.messages.map(message => message.content)), ['问', '答', '新'])
  assert.deepEqual(plain(merged.events.map(event => event.kind)), ['a', 'b', 'c'])
  assert.deepEqual(plain(serverCounts(merged)), { historyVersion: 0, messages: 3, events: 3 })
  assert.deepEqual(plain(sessionCursor(serverCounts(merged))), { historyVersion: 0, messages: 3, events: 3 })
  assert.equal(mergeSessionDelta(undefined, delta), null, '没有缓存不能拼接')
  assert.equal(mergeSessionDelta({ ...cached, history_version: 1 }, delta), null, '历史已被压缩或编辑')
  assert.equal(mergeSessionDelta({ ...cached, messages: cached.messages.slice(0, 1) }, delta), null, '缓存比偏移短')
  const full = detail({ messages: [{ role: 'user', content: '完整' }] })
  assert.equal(mergeSessionDelta(cached, full), full, '完整读取直接使用')
})

test('工具消息按调用 ID 附上参数摘要、失败状态和写入的记忆页', () => {
  const { normalizeMessages, summarizeToolArguments } = model()
  const messages = [
    { role: 'assistant', content: '', tool_calls: [
      { id: 'c1', function: { name: 'run_shell', arguments: JSON.stringify({ command: 'ls   -la\n/tmp' }) } },
      { id: 'c2', function: { name: 'write_wiki_page', arguments: JSON.stringify({ content: '记住这个' }) } },
    ] },
    { role: 'tool', tool_call_id: 'c1', content: '权限不足' },
    { role: 'tool', tool_call_id: 'c2', content: '已写入 1 个 Wiki 页面：代码分析工具偏好。' },
  ]
  const events = [
    { kind: 'tool_status', title: '工具失败', meta: { state: 'failed', tool: 'run_shell', call_id: 'c1' } },
    { kind: 'tool_status', title: '工具完成', meta: { state: 'completed', tool: 'write_wiki_page', call_id: 'c2', pages: ['代码分析工具偏好'] } },
  ]
  const [shell, wiki] = normalizeMessages('A', messages, events)
  assert.equal(shell.toolName, 'run_shell')
  assert.equal(shell.toolSummary, 'ls -la /tmp')
  assert.equal(shell.toolState, 'failed')
  assert.deepEqual(plain(wiki.toolPages), ['代码分析工具偏好'])
  assert.equal(wiki.toolState, 'completed')
  assert.equal(summarizeToolArguments('不是 JSON'), '')
  assert.equal(summarizeToolArguments(JSON.stringify({ query: 'x'.repeat(200) })).length, 121)
  assert.equal(normalizeMessages('A', [{ role: 'tool', tool_call_id: 'old', content: '旧会话' }])[0].toolState, undefined)
})

test('状态与统计只看事件代码和结构化字段，不依赖展示文案', () => {
  const { getAgentActivity, progressEvents, sessionStats } = model()
  const base = { cards: [], running: true, streaming: '', error: '', loading: false, creating: false, mutating: false, backgroundCount: 0, unread: false }
  const session = detail({ run_status: 'running', messages: [{ role: 'user', content: '计划', turn_event_index: 0 }],
    events: [{ kind: 'status', title: '任意新文案', meta: { code: 'replanning' } }] })
  assert.equal(getAgentActivity({ ...base, session }).label, '正在调整计划')
  const events = [
    { kind: 'status', title: '新标题', meta: { code: 'step_running' } },
    { kind: 'status', title: '规划执行失败', meta: { code: 'plan_failed' } },
    { kind: 'status', title: '子任务失败', meta: { code: 'run_failed' } },
    { kind: 'tool_status', title: '工具开始', meta: { state: 'started', tool: 'write_wiki_page' } },
    { kind: 'tool_status', title: '工具完成', meta: { state: 'completed', tool: 'write_wiki_page', pages: ['甲', '乙'] } },
    { kind: 'compression', title: '上下文压缩完成', meta: { state: 'completed' } },
  ]
  const withEvents = detail({ events })
  assert.deepEqual(progressEvents(withEvents).map(event => event.meta.code ?? event.kind),
    ['step_running', 'tool_status', 'tool_status', 'compression'])
  assert.ok(progressEvents(withEvents, true).some(event => event.meta.code === 'run_failed'), '子会话保留失败原因')
  const stats = sessionStats(withEvents)
  assert.equal(stats.toolTotal, 1)
  assert.equal(stats.memoryWrites, 1)
  assert.deepEqual(plain(stats.memoryPages), ['甲', '乙'])
  assert.equal(stats.compressions.length, 1)
})

test('已读记录每个会话只保留最近 20 条，并清掉已不存在的会话', () => {
  const storage = new Map()
  const { saveReadReplies, loadReadReplies } = model(storage)
  const many = Array.from({ length: 30 }, (_, index) => `r${index}`)
  const saved = saveReadReplies({ A: many, B: ['x'], C: [] }, new Set(['A', 'C']))
  assert.deepEqual(plain(Object.keys(saved)), ['A'])
  assert.equal(saved.A.length, 20)
  assert.equal(saved.A[0], 'r10')
  assert.deepEqual(plain(loadReadReplies()), plain(saved))
})
