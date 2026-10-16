const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')

/** 加载真实输入组件，可传入测量节点验证输入高度与键盘交互。 */
function composer(overrides = {}, element = null) {
  const exports = {}, sent = [], effects = []
  const source = ts.transpileModule(readFileSync('src/features/composer/Composer.tsx', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  vm.runInNewContext(source, { exports, require: name => name === 'react'
    ? { useRef: () => ({ current: element }), useLayoutEffect: effect => effects.push(effect) } : require(name) })
  const tree = exports.Composer({ value: '  检查代码  ', onChange: () => {}, onSend: value => sent.push(value),
    disabled: false, loading: false, running: false, planEnabled: false, onTogglePlan: () => {},
    contextUsage: null, compressionNotice: null, ...overrides })
  const nodes = []
  function visit(node) {
    if (!node || typeof node !== 'object') return
    nodes.push(node)
    const children = node.props?.children
    for (const child of Array.isArray(children) ? children : [children]) visit(child)
  }
  visit(tree)
  return { sent, form: tree, input: nodes.find(node => node.type === 'textarea'), send: nodes.find(node => node.props?.type === 'submit'), resize: () => effects.forEach(effect => effect()) }
}

test('输入默认单行，随内容增高、限制长内容高度，清空后恢复单行', () => {
  let contentHeight = 25
  const element = { style: {}, get scrollHeight() { assert.equal(this.style.height, 'auto'); return contentHeight } }
  const view = composer({ value: '' }, element)
  assert.equal(view.input.props.rows, 1)
  view.resize()
  assert.equal(element.style.height, '25px')
  contentHeight = 75
  view.resize()
  assert.equal(element.style.height, '75px')
  contentHeight = 900
  view.resize()
  assert.equal(element.style.height, '180px')
  contentHeight = 25
  view.resize()
  assert.equal(element.style.height, '25px')
})

test('重设计后的输入框保留中文输入法确认、换行与 Enter 发送语义', () => {
  const view = composer()
  let prevented = 0
  const key = changes => ({ key: 'Enter', shiftKey: false, nativeEvent: {}, preventDefault: () => prevented++, ...changes })
  view.input.props.onKeyDown(key({ nativeEvent: { isComposing: true } }))
  view.input.props.onKeyDown(key({ nativeEvent: { keyCode: 229 } }))
  view.input.props.onKeyDown(key({ shiftKey: true }))
  assert.equal(view.sent.length, 0)
  assert.equal(prevented, 0)
  view.input.props.onKeyDown(key({}))
  assert.deepEqual(view.sent, ['检查代码'])
  assert.equal(prevented, 1)
})

test('运行中的草稿仍可编辑，但按钮和表单不能再次发送', () => {
  const view = composer({ disabled: true, running: true })
  assert.equal(view.input.props.disabled, false)
  assert.equal(view.send.props.disabled, true)
  view.form.props.onSubmit({ preventDefault() {} })
  assert.deepEqual(view.sent, [])
  assert.equal(composer({ loading: true }).input.props.disabled, true)
  assert.equal(composer({ value: '  ' }).send.props.disabled, true)
})
