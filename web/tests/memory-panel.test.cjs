const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')

/** 驱动真实面板的局部状态；数据操作仍通过传入的 ViewModel 执行。 */
function panel() {
  const page = { name: '项目记忆', content: '已保存正文', description: '项目摘要', revision: 1,
    tags: [], related: [], links: ['关联页面'], backlinks: [], sources: [], status: 'ready' }
  const selections = [], sessions = [], slots = []
  let cursor = 0, nodes = []
  const model = { detail: page, selected: page.name, query: '项目', offset: 0,
    pages: { items: [page], total: 1 }, error: '', draft: null,
    status: { pending: 0, repairs: [{ name: '整理失败页面', status: 'failed' }],
      extractions: [{ session_id: 'chat-1', status: 'failed', error: '提取失败' }] },
    report: { total_pages: 1, issues: [{ page_name: '待检查页面', detail: '存在失效关联' }] },
    selectPage(name) { selections.push(name); if (name !== this.selected) { this.selected = name; this.error = '' } },
    startEdit() { model.draft = { ...page, tagsText: '', relatedText: '' } },
    updateDraft(change) { model.draft = { ...model.draft, ...change } },
    cancelEdit() { assert.fail('返回列表不能取消草稿') },
    save() { assert.fail('导航不能提交草稿') },
  }
  const exports = {}
  const source = ts.transpileModule(readFileSync('src/features/memory/MemoryPanel.tsx', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  vm.runInNewContext(source, { exports, requestAnimationFrame: fn => fn(), require: name => {
    if (name === 'react') return {
      useState(initial) { const i = cursor++; slots[i] ??= { value: initial }; return [slots[i].value, value => { slots[i].value = value }] },
      useRef(initial) { return (slots[cursor++] ??= { current: initial }) },
    }
    if (['./useMemoryWorkspace', './memory.css', 'react-markdown', 'remark-gfm'].includes(name)) return {}
    return require(name)
  } })
  /** 遍历 JSX，保留真实控件事件供测试点击。 */
  function visit(node) {
    if (Array.isArray(node)) { node.forEach(visit); return }
    if (!node || typeof node !== 'object') return
    nodes.push(node)
    visit(node.props?.children)
  }
  return { model, selections, sessions,
    render() { cursor = 0; nodes = []; visit(exports.MemoryPanel({ model, onOpenSession: id => sessions.push(id) })); return this },
    find(match) { const node = nodes.find(match); assert.ok(node, '应找到指定控件'); return node },
    withClass(name) { return this.find(node => node.props?.className?.split(' ').includes(name)) },
  }.render()
}

test('列表进入详情后可返回，搜索、错误和未保存正文始终保留', () => {
  const view = panel()
  assert.equal(view.withClass('memory-layout').props['data-active-pane'], 'list')
  view.withClass('memory-list-item').props.onClick()
  view.render()
  assert.equal(view.withClass('memory-layout').props['data-active-pane'], 'detail')
  view.find(node => node.type === 'button' && node.props.onClick === view.model.startEdit).props.onClick()
  view.render()
  view.find(node => node.type === 'textarea').props.onChange({ target: { value: '未保存的编辑内容' } })
  view.model.error = '保存失败，请稍后重试'
  view.render().withClass('memory-back').props.onClick()
  view.render()
  assert.equal(view.withClass('memory-layout').props['data-active-pane'], 'list')
  assert.equal(view.selections.length, 1, '返回不得重新选择页面或清理业务状态')
  assert.equal(view.find(node => node.type === 'input' && node.props.placeholder === '名称、标签或正文').props.value, '项目')
  assert.equal(view.find(node => node.type === 'textarea').props.value, '未保存的编辑内容')
  assert.ok(view.withClass('memory-error').props.children[0].props.children.includes('保存失败'))
  view.withClass('memory-list-item').props.onClick()
  view.render()
  assert.equal(view.withClass('memory-layout').props['data-active-pane'], 'detail')
  assert.equal(view.model.error, '保存失败，请稍后重试')
  assert.equal(view.find(node => node.type === 'textarea').props.value, '未保存的编辑内容')
})

test('任务默认收起，整理、关系和检查结果入口都能直接打开详情', () => {
  const view = panel()
  assert.equal(view.withClass('memory-tasks').props.open, undefined)
  assert.equal(view.withClass('memory-lint').props.open, true)
  view.withClass('memory-progress').props.children[0].props.onClick()
  view.render()
  assert.equal(view.withClass('memory-layout').props['data-active-pane'], 'detail')
  assert.equal(view.selections.at(-1), '整理失败页面')
  view.withClass('memory-back').props.onClick()
  view.render().withClass('memory-relation-links').props.children[0][0].props.onClick()
  view.render()
  assert.equal(view.withClass('memory-layout').props['data-active-pane'], 'detail')
  assert.equal(view.selections.at(-1), '关联页面')
  view.withClass('memory-back').props.onClick()
  view.render().withClass('memory-lint-content').props.children[0].props.children[0].props.onClick()
  view.render()
  assert.equal(view.withClass('memory-layout').props['data-active-pane'], 'detail')
  assert.equal(view.selections.at(-1), '待检查页面')
  view.withClass('memory-extraction').props.children[0].props.onClick()
  assert.deepEqual(view.sessions, ['chat-1'])
})

test('错误、任务与检查报告共用一个辅助区，避免高度逐项累加', () => {
  const view = panel()
  view.model.error = '连接断开'
  view.render()
  const children = view.withClass('memory-auxiliary').props.children
  for (const name of ['memory-error', 'memory-tasks', 'memory-lint']) {
    assert.ok(children.includes(view.withClass(name)), `${name} 应共用辅助区`)
  }
})

test('列表详情切换聚焦不会滚动祖先，当前条目被筛除时返回搜索框', () => {
  const view = panel(), focused = []
  const target = name => ({ focus(options) { focused.push([name, options.preventScroll]) } })
  view.withClass('memory-back').props.ref.current = { ...target('返回'), getClientRects: () => [{}] }
  view.withClass('memory-list').props.ref.current = {
    querySelector(selector) { return selector === 'input' ? target('搜索') : view.model.pages.items.length ? target('当前条目') : null },
  }
  view.withClass('memory-list-item').props.onClick()
  view.render().withClass('memory-back').props.onClick()
  view.render().withClass('memory-list-item').props.onClick()
  view.model.pages.items = []
  view.model.query = '无匹配'
  view.render().withClass('memory-back').props.onClick()
  assert.deepEqual(focused, [['返回', true], ['当前条目', true], ['返回', true], ['搜索', true]])
})
