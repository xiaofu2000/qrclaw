const { test } = require('node:test')
const assert = require('node:assert/strict')
const { loadModule, hookRuntime, collectNodes, textOf } = require('./helpers/load.cjs')

const makePage = (name, revision = 1, content = `${name} 正文`) => ({
  name, revision, content, description: `${name} 摘要`,
  tags: ['QRClaw'], related: [], sources: [], links: [], backlinks: [],
  status: 'ready', created_at: '2026-11-26T00:00:00', updated_at: '2026-11-26T00:00:00', source_count: 1,
})

function createMockApi() {
  const pages = { 架构设计: makePage('架构设计', 2, '当前第二版正文') }
  const history = {
    架构设计: [
      {
        name: '架构设计', revision: 1, content: '早期第一版正文', description: '初版摘要',
        tags: ['QRClaw'], related: [], status: 'ready', source_count: 1, char_count: 7,
        created_at: '2026-11-25T00:00:00', updated_at: '2026-11-25T00:00:00',
      },
    ],
  }
  const storeVersions = [
    { id: 'sv_20261126_010000_ab12', created_at: 1764100000, reason: '切换前备份', page_count: 3, total_chars: 120 },
  ]
  const dreams = {}
  const calls = []

  return {
    pages, history, storeVersions, dreams, calls,
    async listWikiPages(query, offset) {
      const items = Object.values(pages).filter(p => p.name.includes(query))
      return { items: items.slice(offset, offset + 20), total: items.length, offset, limit: 20 }
    },
    async getWikiStatus() {
      const active = Object.values(dreams).filter(d => ['pending', 'running'].includes(d.status)).length
      return { pending: active, active_dreams: active, repairs: [], extractions: [] }
    },
    async getWikiPage(name) {
      if (!pages[name]) throw Object.assign(new Error('不存在'), { status: 404 })
      return { ...pages[name] }
    },
    async editWikiPage(name, draft) {
      pages[name] = { ...pages[name], ...draft, revision: pages[name].revision + 1 }
      return pages[name]
    },
    async deleteWikiPage(name) { delete pages[name]; return { deleted: true } },
    async repairWikiPage(name) { return pages[name] },
    async lintWiki() { return { total_pages: Object.keys(pages).length, issues: [] } },
    async listSessions() {
      return [
        { id: 'sess-1', title: '讨论压缩架构', message_count: 8, updated_at: '2026-11-26 10:00' },
        { id: 'sess-2', title: '前端样式细节', message_count: 4, updated_at: '2026-11-26 11:00' },
      ]
    },
    async listPageHistory(name) {
      calls.push(['listPageHistory', name])
      const items = history[name] || []
      return { name, items, total: items.length }
    },
    async getPageHistoryVersion(name, revision) {
      calls.push(['getPageHistoryVersion', name, revision])
      return (history[name] || []).find(item => item.revision === revision)
    },
    async restoreWikiPage(name, revision, currentRevision) {
      calls.push(['restoreWikiPage', name, revision, currentRevision])
      const snap = (history[name] || []).find(item => item.revision === revision)
      pages[name] = {
        ...pages[name],
        content: snap.content,
        description: snap.description,
        revision: (currentRevision || pages[name].revision) + 1,
        sources: [{ id: 'manual-1', kind: 'manual', state: 'valid', body: snap.content }],
      }
      return pages[name]
    },
    async listStoreVersions() {
      calls.push(['listStoreVersions'])
      return { items: [...storeVersions], total: storeVersions.length }
    },
    async restoreStoreVersion(versionId) {
      calls.push(['restoreStoreVersion', versionId])
      pages['恢复后的旧页'] = makePage('恢复后的旧页', 5, '整库回退恢复的正文')
      return { restored_version: versionId, archived_version: 'sv_auto', page_count: Object.keys(pages).length }
    },
    async createWikiDream(payload) {
      calls.push(['createWikiDream', payload])
      const dream = {
        id: 'drm_test_01',
        status: 'completed',
        session_ids: payload.session_ids || ['sess-1'],
        instructions: payload.instructions || '',
        usage: { input_tokens: 320, output_tokens: 140, total_tokens: 460 },
        steps: 2,
        error: null,
        created_at: 1764110000,
        ended_at: 1764110020,
        applied_at: null,
        archived_at: null,
        diff: {
          has_copy: true,
          added: [{ name: 'QRClaw 压缩机制', description: '压缩与近期预算', tags: ['QRClaw'], char_count: 42 }],
          removed: [{ name: '旧杂项页', description: '过期页', tags: [], char_count: 15 }],
          modified: [{ name: '架构设计', description: '更新架构', tags: ['QRClaw'], before_chars: 7, after_chars: 18 }],
          unchanged_count: 0,
          before_page_count: 2,
          after_page_count: 2,
          before_total_chars: 22,
          after_total_chars: 60,
        },
      }
      dreams[dream.id] = dream
      return dream
    },
    async listWikiDreams() {
      const items = Object.values(dreams).filter(d => !d.archived_at)
      return { items, total: items.length }
    },
    async getWikiDream(id) { return { ...dreams[id] } },
    async listWikiDreamPages() {
      return {
        items: [
          { name: 'QRClaw 压缩机制', description: '压缩与近期预算', tags: ['QRClaw'], related: [], char_count: 42, change_type: 'added' },
          { name: '架构设计', description: '更新架构', tags: ['QRClaw'], related: [], char_count: 18, change_type: 'modified' },
        ],
        total: 2,
      }
    },
    async getWikiDreamPage(_id, name) {
      return {
        name,
        content: `${name} 新库整理正文`,
        description: '整理后摘要',
        tags: ['QRClaw'],
        related: [],
        char_count: 18,
        change_type: name === '架构设计' ? 'modified' : 'added',
        original: name === '架构设计' ? { content: '当前第二版正文', description: '旧', tags: [], related: [], revision: 2 } : null,
        diff: name === '架构设计' ? '-当前第二版正文\n+架构设计 新库整理正文' : '',
      }
    },
    async getWikiDreamTranscript(id) {
      return {
        dream_id: id,
        total: 1,
        offset: 0,
        limit: 50,
        items: [{
          step: 1,
          at: 1764110010,
          content: '先查看全量目录并合并重复项',
          tool_calls: [{ id: 'tc1', name: 'memory_view', arguments: '{}' }],
          tool_results: [{ tool_call_id: 'tc1', name: 'memory_view', result: '{"total_pages":1}' }],
          usage: { input_tokens: 320, output_tokens: 140, total_tokens: 460 },
        }],
      }
    },
    async cancelWikiDream(id) {
      calls.push(['cancelWikiDream', id])
      dreams[id] = { ...dreams[id], status: 'canceled' }
      return dreams[id]
    },
    async previewApplyWikiDream(id) {
      calls.push(['previewApplyWikiDream', id])
      return {
        dream_id: id,
        status: 'completed',
        can_apply: true,
        concurrent_modified: [{ name: '并发编辑页', base_revision: 1, current_revision: 2, description: '并发改动' }],
        concurrent_added: [],
        excluded_deleted: ['已删旧页'],
        warning: '整理期间有页面被主动删除，切换时将自动排除同名页面。',
        source_notice: '做梦整理生成的页面标记为「来源未追溯（legacy）」。',
      }
    },
    async applyWikiDream(id, carryOver) {
      calls.push(['applyWikiDream', id, carryOver])
      dreams[id] = { ...dreams[id], applied_at: 1764110099 }
      pages['QRClaw 压缩机制'] = { ...makePage('QRClaw 压缩机制', 1, '新整理正文'), status: 'legacy', source_count: 0 }
      return { applied: true, dream_id: id, archived_version: 'sv_before_apply', page_count: 2 }
    },
    async archiveWikiDream(id) {
      calls.push(['archiveWikiDream', id])
      dreams[id] = { ...dreams[id], archived_at: 1764110120 }
      return dreams[id]
    },
  }
}

function mountWorkspace(api) {
  const runtime = hookRuntime(), timers = new Map()
  let timerId = 0
  const document = { hidden: false, addEventListener() {}, removeEventListener() {} }
  const { useMemoryWorkspace } = loadModule('src/features/memory/useMemoryWorkspace.ts', {
    mocks: { react: runtime.react },
    globals: {
      document, AbortController,
      setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId },
      clearTimeout: id => timers.delete(id),
    },
  })
  const render = () => runtime.render(() => useMemoryWorkspace(api, true))
  render()
  return {
    render,
    async flush() {
      let view
      for (let i = 0; i < 5; i++) {
        await new Promise(resolve => setImmediate(resolve))
        view = render()
      }
      return view
    },
    close: runtime.close,
  }
}

test('做梦完整流程：发起整理、查看对比与过程回看、预览并发改动并切换到新记忆', async () => {
  const api = createMockApi()
  const hook = mountWorkspace(api)
  let view = await hook.flush()

  await view.openDreamPanel()
  view = await hook.flush()
  assert.equal(view.dreamOpen, true)
  assert.equal(view.dreamSessions.length, 2)

  await view.startDream({ sessionIds: ['sess-1'], instructions: '重点关注压缩机制' })
  view = await hook.flush()
  assert.equal(view.activeDream.id, 'drm_test_01')
  assert.equal(view.dreamPages.length, 2)
  assert.equal(view.dreamTranscript.items.length, 1)

  await view.inspectDreamPage('架构设计')
  view = await hook.flush()
  assert.equal(view.selectedDreamPage.change_type, 'modified')
  assert.match(view.selectedDreamPage.diff, /\+架构设计 新库整理正文/)

  await view.openApplyPreview()
  view = await hook.flush()
  assert.equal(view.applyPreview.can_apply, true)
  assert.deepEqual(view.applyPreview.excluded_deleted, ['已删旧页'])

  await view.confirmApplyDream(true)
  view = await hook.flush()
  assert.deepEqual(api.calls.find(c => c[0] === 'applyWikiDream'), ['applyWikiDream', 'drm_test_01', true])
  assert.ok(view.pages.items.some(p => p.name === 'QRClaw 压缩机制'))
  assert.equal(view.applyPreview, null)

  await view.discardDream('drm_test_01')
  view = await hook.flush()
  assert.equal(view.dreams.length, 0)
  assert.equal(view.activeDream, null)
  hook.close()
})

test('单页历史预览与回滚、整库版本回退均携带版本校验并刷新当前视图', async () => {
  const api = createMockApi()
  const hook = mountWorkspace(api)
  let view = await hook.flush()
  assert.equal(view.detail.name, '架构设计')
  assert.equal(view.detail.revision, 2)

  await view.loadPageHistory('架构设计')
  view = await hook.flush()
  assert.equal(view.pageHistory.length, 1)

  await view.previewHistoryVersion(1)
  view = await hook.flush()
  assert.equal(view.historyPreview.content, '早期第一版正文')

  await view.restorePageVersion(1)
  view = await hook.flush()
  assert.deepEqual(api.calls.find(c => c[0] === 'restoreWikiPage'), ['restoreWikiPage', '架构设计', 1, 2])
  assert.equal(view.detail.content, '早期第一版正文')
  assert.equal(view.detail.revision, 3)
  assert.equal(view.historyPreview, null)

  await view.openStoreVersions()
  view = await hook.flush()
  assert.equal(view.storeVersionsOpen, true)
  assert.equal(view.storeVersions.length, 1)

  await view.restoreStoreVersion('sv_20261126_010000_ab12')
  view = await hook.flush()
  assert.equal(view.storeVersionsOpen, false)
  assert.ok(view.pages.items.some(p => p.name === '恢复后的旧页'))
  hook.close()
})

test('MemoryPanel 界面交互：做梦弹窗、来源未追溯提示、过程回看切换与单页历史二次确认', () => {
  const slots = []
  let cursor = 0, nodes = []
  const submitted = [], applied = [], restored = []

  const page = makePage('架构设计', 2, '当前正文')
  const model = {
    detail: page, selected: page.name, query: '', offset: 0,
    pages: { items: [page], total: 1 }, error: '', draft: null,
    status: { pending: 1, active_dreams: 1, repairs: [], extractions: [] },
    report: null,
    dreamOpen: true,
    dreams: [{ id: 'drm_1', status: 'completed', steps: 3, created_at: 1764110000, session_ids: ['sess-1'], usage: { input_tokens: 100, output_tokens: 50 } }],
    activeDream: {
      id: 'drm_1', status: 'completed', steps: 3, created_at: 1764110000, ended_at: 1764110030,
      applied_at: null, archived_at: null, session_ids: ['sess-1'], instructions: '', error: null,
      usage: { input_tokens: 100, output_tokens: 50 },
      diff: { has_copy: true, added: [{ name: '新页', description: '', tags: [], char_count: 10 }], removed: [], modified: [], unchanged_count: 1, before_page_count: 1, after_page_count: 2, before_total_chars: 10, after_total_chars: 20 },
    },
    dreamPages: [{ name: '新页', description: '摘要', tags: ['QRClaw'], related: [], char_count: 10, change_type: 'added' }],
    selectedDreamPage: { name: '新页', content: '新页内容', description: '摘要', tags: ['QRClaw'], related: [], char_count: 10, change_type: 'added', original: null, diff: '' },
    dreamTranscript: {
      dream_id: 'drm_1', total: 1, offset: 0, limit: 50,
      items: [{ step: 1, at: 1764110010, content: '整理步骤说明', tool_calls: [{ id: 'c1', name: 'memory_view', arguments: '{}' }], tool_results: [{ tool_call_id: 'c1', name: 'memory_view', result: 'ok' }], usage: { input_tokens: 100, output_tokens: 50 } }],
    },
    applyPreview: {
      dream_id: 'drm_1', status: 'completed', can_apply: true,
      concurrent_modified: [{ name: '并发页', base_revision: 1, current_revision: 2, description: '' }],
      concurrent_added: [], excluded_deleted: [], warning: '',
      source_notice: '做梦整理生成的页面标记为「来源未追溯（legacy）」。',
    },
    dreamSessions: [{ id: 'sess-1', title: '主会话一', message_count: 6, updated_at: '2026-11-26' }],
    pageHistory: [{ name: '架构设计', revision: 1, description: '旧版', tags: [], related: [], status: 'ready', source_count: 1, char_count: 8, created_at: '2026-11-25', updated_at: '2026-11-25' }],
    historyPreview: null,
    storeVersionsOpen: false,
    storeVersions: [],
    selectPage() {},
    startDream(opts) { submitted.push(opts) },
    confirmApplyDream(carry) { applied.push(carry) },
    restorePageVersion(rev) { restored.push(rev) },
  }

  const react = {
    useState(initial) { const i = cursor++; slots[i] ??= { value: initial }; return [slots[i].value, value => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value }] },
    useRef(initial) { return (slots[cursor++] ??= { current: initial }) },
  }
  const exports = loadModule('src/features/memory/MemoryPanel.tsx', {
    globals: { requestAnimationFrame: fn => fn() },
    mocks: { react, './useMemoryWorkspace': {}, 'react-markdown': {}, 'remark-gfm': {} },
  })
  const render = () => { cursor = 0; nodes = collectNodes(exports.MemoryPanel({ model, onOpenSession() {} })) }
  const withClass = cls => nodes.find(n => n.props?.className?.split(' ').includes(cls))

  render()
  // 验证「来源未追溯」提示在切换确认区清晰展示
  assert.match(textOf(withClass('memory-dream-apply-confirm')), /来源未追溯/)

  // 填写方向说明并提交发起做梦
  withClass('memory-dream-instructions').props.onChange({ target: { value: '只整理编码规范' } })
  render()
  withClass('memory-dream-create').props.onSubmit({ preventDefault() {} })
  assert.deepEqual(JSON.parse(JSON.stringify(submitted)), [{ sessionIds: null, instructions: '只整理编码规范' }])

  // 切换到「整理过程回看」Tab 能看到步骤记录与工具调用
  const tabs = nodes.filter(n => n.props?.role === 'tab')
  tabs[1].props.onClick()
  render()
  assert.match(textOf(withClass('memory-dream-transcript')), /整理步骤说明/)
  assert.match(textOf(withClass('memory-dream-transcript')), /memory_view/)

  // 确认切换默认携带 carryOver = true
  withClass('memory-dream-confirm-apply').props.onClick()
  assert.deepEqual(applied, [true])

  // 单页历史回滚需经过二次确认才真正触发 restorePageVersion
  withClass('memory-trigger-history-restore').props.onClick()
  render()
  assert.equal(restored.length, 0)
  withClass('memory-confirm-history-restore').props.onClick()
  assert.deepEqual(restored, [1])
})
