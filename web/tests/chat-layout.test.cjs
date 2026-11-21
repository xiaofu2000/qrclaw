const { test } = require('node:test')
const assert = require('node:assert/strict')
const { loadModule, collectNodes, textOf: text } = require('./helpers/load.cjs')

/** 渲染真实聊天面板的元素树，不启动浏览器，只检查用户与 QRClaw 消息的分组和顺序。 */
function render(timeline, overrides = {}) {
  const exports = loadModule('src/features/chat/ChatPanel.tsx', { mocks: {
    react: { useRef: initial => ({ current: initial }), useState: initial => [initial, () => {}], useLayoutEffect: () => {}, useEffect: () => {} },
    'react-markdown': { default: 'Markdown' }, 'remark-gfm': { default: {} }, './ExecutionReport': { ExecutionReport: 'ExecutionReport' },
  } })
  const tree = exports.ChatPanel({ sessionId: 'A', title: '会话标题', messageCount: 5, timeline, streaming: '', running: false, queued: false,
    loading: false, mutating: false, visible: true, unreadReplyId: null, onReplyRead: () => {}, onSelectSubAgent: () => {},
    onEditLatestMessage: () => {}, onDeleteLatestMessage: () => {}, assistantAvatar: { type: 'Avatar', props: {} }, ...overrides })
  return collectNodes(tree)
}

test('用户消息在右侧气泡，QRClaw 的回复、连续工具调用和子任务按顺序收进同一条左侧消息', () => {
  const timeline = [
    { id: 'm0', uuid: 'u1', role: 'user', content: '帮我看看 skill' },
    { id: 'm1', role: 'assistant', content: '先查一下目录。' },
    { id: 'm2', role: 'tool', toolName: 'run_shell', content: 'ls' },
    { id: 'm3', role: 'tool', toolName: 'run_shell', content: 'cat' },
    { id: 'm4', role: 'tool', toolName: 'write_wiki_page', content: '✅ 已写入 1 个 Wiki 页面。' },
    { id: 'c1', childSessionId: 'child', agentId: 'step-1', task: '读取配置', state: 'completed', afterMessageIndex: 4 },
    { id: 'm5', role: 'assistant', content: '结论：没适配。' },
  ]
  const nodes = render(timeline, { onOpenMemory: () => {} })
  const articles = nodes.filter(node => node.type === 'article')
  assert.deepEqual(articles.map(node => node.props.className), ['chat-message user', 'chat-message assistant'])
  const [user, assistant] = articles
  assert.ok(text(user).includes('帮我看看 skill'))
  assert.equal(assistant.props.children[0].type, 'Avatar')
  const inside = []
  ;(function walk(value) {
    if (Array.isArray(value)) return value.forEach(walk)
    if (value && typeof value === 'object' && value.props) { inside.push(value); walk(value.props.children) }
  })(assistant)
  const runs = inside.filter(node => node.props.className === 'turn-run')
  assert.equal(runs.length, 1, '连续三次工具调用合并成一段')
  assert.ok(text(runs[0]).includes('调用了 3 次工具'))
  assert.ok(text(runs[0]).includes('run_shell ×2'))
  assert.ok(text(assistant).includes('写入记忆 1 次'))
  assert.equal(inside.filter(node => node.props.className === 'subagent-card').length, 1)
  const order = ['先查一下目录。', '调用了 3 次工具', '读取配置', '结论：没适配。'].map(part => text(assistant).indexOf(part))
  assert.deepEqual([...order].sort((a, b) => a - b), order, '回复与过程保持原始顺序')
})

test('最后一条用户消息保留编辑与删除，运行中只显示 QRClaw 的等待状态', () => {
  const idle = render([{ id: 'm0', uuid: 'u1', role: 'user', content: '你好' }], { latestUserMessageId: 'u1' })
  assert.ok(idle.some(node => node.props['aria-label'] === '编辑最后一条用户消息'))
  const busy = render([{ id: 'm0', uuid: 'u1', role: 'user', content: '你好' }], { latestUserMessageId: 'u1', running: true })
  assert.ok(!busy.some(node => node.props['aria-label'] === '编辑最后一条用户消息'))
  const pending = busy.find(node => node.props.className === 'pending-response')
  assert.ok(pending, 'QRClaw 消息里显示正在处理')
  assert.ok(busy.some(node => node.type === 'article' && node.props.className === 'chat-message assistant'))
})
