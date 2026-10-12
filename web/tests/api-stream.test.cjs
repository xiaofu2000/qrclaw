const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')

const source = readFileSync('src/lib/api.ts', 'utf8').replace(/^const baseUrl = .*$/m, "const baseUrl = ''")
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function clientWithFrames(frames) {
  const bytes = new TextEncoder().encode(frames)
  const chunks = [...bytes].map(byte => Uint8Array.of(byte))
  const requests = []
  const exports = {}
  vm.runInNewContext(compiled, {
    exports,
    TextDecoder,
    fetch: async (url, options) => {
      requests.push({ url, options })
      return {
        ok: true,
        body: { getReader: () => ({
          read: async () => chunks.length ? { done: false, value: chunks.shift() } : { done: true },
          releaseLock() {},
          cancel: async () => {},
        }) },
      }
    },
  })
  return { api: exports.createApiClient(), requests }
}

test('聊天 SSE 按字节切分后仍按顺序还原中文片段', async () => {
  const frame = content => `data: ${JSON.stringify({ object: 'chat.completion.chunk', choices: [{ delta: { content } }] })}\n\n`
  const { api, requests } = clientWithFrames(`${frame('你')}${frame('好')}data: [DONE]\n\n`)
  const deltas = []
  await api.chatStream('A', '你好', delta => deltas.push(delta))
  assert.deepEqual(deltas, ['你', '好'])
  assert.equal(requests.length, 1)
  assert.equal(JSON.parse(requests[0].options.body).stream, true)
})

test('没有结束标记时报告断线且不重发任务', async () => {
  const { api, requests } = clientWithFrames('data: {"choices":[{"delta":{"content":"部分"}}]}\n\n')
  await assert.rejects(api.chatStream('A', '执行', () => {}), /提前断开/)
  assert.equal(requests.length, 1)
})

test('过程 SSE 读取事件序号与内容', async () => {
  const { api, requests } = clientWithFrames('id: 3\nevent: progress\ndata: {"kind":"tool_status","title":"工具开始","content":"read_file"}\n\n')
  const events = []
  await api.streamEvents('A', 2, (index, event) => events.push([index, event.title]))
  assert.deepEqual(events, [[3, '工具开始']])
  assert.match(requests[0].url, /after=2$/)
})
