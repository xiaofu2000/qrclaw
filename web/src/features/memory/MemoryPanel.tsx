import { useEffect, useRef, useState } from 'react'
import {
  ArrowUpRight, BookOpen, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight,
  Clock3, FileText, History, Link2, Pencil, RefreshCw, RotateCcw, ScanLine,
  Search, Sparkles, Trash2, X,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useMemoryWorkspace } from './useMemoryWorkspace'
import type { createApiClient } from '../../lib/api'
import './memory.css'

const labels: Record<string, string> = {
  legacy: '来源未追溯', ready: '可用', pending: '等待整理', processing: '整理中',
  failed: '整理失败', inactive: '来源已撤销', completed: '已完成', idle: '暂无待提取正文',
  running: '整理中', canceled: '已取消', added: '新增', modified: '已修改', unchanged: '未变更',
}
/** 统一展示来源时间和页面更新时间。 */
const timeText = (value?: number | string | null) => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN') : '暂无'

/** 将 Wiki 链接转为页面入口；渲染仍使用 Markdown 的安全链接处理。 */
function linkedMarkdown(content: string) {
  return content.replace(/\[\[([^\]]+)\]\]/g, (_whole, link: string) => {
    const [target, alias] = link.split('|', 2)
    const name = target.split('#', 1)[0].trim()
    return name ? `[${(alias || target).replace(/[[\]]/g, '')}](#wiki-page=${encodeURIComponent(name)})` : target
  })
}

/** 记忆工作区入口：ViewModel 保存逻辑，面板只负责交互展示。 */
export function MemoryWorkspace({ api, visible, activityKey, focusPage, onOpenSession }: {
  api: ReturnType<typeof createApiClient>; visible: boolean; activityKey: number; focusPage?: string; onOpenSession: (id: string) => void
}) {
  const model = useMemoryWorkspace(api, visible, activityKey)
  // 从聊天或属性栏点开某页记忆时直接选中它；每次打开只选一次，之后用户可自由切换。
  const focusedRef = useRef('')
  useEffect(() => {
    if (!visible) { focusedRef.current = ''; return }
    if (focusPage && focusedRef.current !== focusPage) {
      focusedRef.current = focusPage
      model.selectPage(focusPage)
    }
  })
  return <div hidden={!visible} className="memory-workspace" role="main" aria-label="记忆管理"><MemoryPanel model={model} onOpenSession={onOpenSession} /></div>
}

/** 记忆列表、详情、来源、编辑、单页历史、整库版本和记忆做梦面板，所有业务操作委托 ViewModel。 */
export function MemoryPanel({ model, onOpenSession }: {
  model: ReturnType<typeof useMemoryWorkspace>; onOpenSession: (id: string) => void
}) {
  const [confirmDelete, setConfirmDelete] = useState('')
  const [activePane, setActivePane] = useState<'list' | 'detail'>('list')
  const [dreamInstructions, setDreamInstructions] = useState('')
  const [pickedSessions, setPickedSessions] = useState<string[]>([])
  const [dreamTab, setDreamTab] = useState<'diff' | 'transcript'>('diff')
  const [carryOver, setCarryOver] = useState(true)
  const [confirmStoreId, setConfirmStoreId] = useState('')
  const [confirmHistoryRev, setConfirmHistoryRev] = useState<number | null>(null)
  const listRef = useRef<HTMLElement>(null)
  const backRef = useRef<HTMLButtonElement>(null)
  const page = model.detail
  const draft = model.draft
  const content = page ? linkedMarkdown(page.content) : ''
  const repairJobs = model.status?.repairs.filter(job => ['pending', 'processing', 'failed'].includes(job.status)) || []
  const historyItems = model.pageHistory || []
  const activeDream = model.activeDream || null
  const dreamPages = model.dreamPages || []
  const dreams = model.dreams || []
  const storeVersions = model.storeVersions || []
  const dreamSessions = model.dreamSessions || []

  /** 窄屏打开正文后将焦点交给返回按钮，业务选择仍由 ViewModel 处理。 */
  function openPage(name: string) {
    model.selectPage(name)
    setActivePane('detail')
    requestAnimationFrame(() => { if (backRef.current?.getClientRects().length) backRef.current.focus({ preventScroll: true }) })
  }

  /** 返回只切换展示，保留搜索、错误和正在编辑的草稿。 */
  function showList() {
    setActivePane('list')
    requestAnimationFrame(() => {
      const target = listRef.current?.querySelector<HTMLButtonElement>('[aria-current="page"]')
        || listRef.current?.querySelector<HTMLInputElement>('input')
      target?.focus({ preventScroll: true })
    })
  }

  function toggleSession(id: string) {
    setPickedSessions(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id])
  }

  return <>
    <header className="memory-header">
      <h1>记忆库</h1>
      <div className="memory-actions">
        <button className="memory-dream-trigger" onClick={() => void model.openDreamPanel?.()} disabled={model.mutating}>
          <Sparkles size={15} aria-hidden="true" />整理记忆（做梦）
        </button>
        <button className="memory-versions-trigger" onClick={() => void model.openStoreVersions?.()} disabled={model.mutating}>
          <History size={15} aria-hidden="true" />库版本回退
        </button>
        <button onClick={model.refresh} disabled={model.mutating}><RefreshCw size={15} aria-hidden="true" />刷新</button>
        <button onClick={() => void model.lint()} disabled={model.mutating}><ScanLine size={15} aria-hidden="true" />检查记忆</button>
      </div>
    </header>
    <div className="memory-auxiliary">
      {model.error && <div className="memory-notice memory-error" role="alert"><span>{model.error}</span><button onClick={model.refresh}>重新载入</button></div>}
      {model.status && <details className="memory-tasks memory-disclosure">
        <summary><span className="memory-sync-label" role="status">{model.status.pending ? <Clock3 size={15} aria-hidden="true" /> : <CheckCircle2 size={15} aria-hidden="true" />}
          {model.status.pending ? `${model.status.pending} 项待处理` : '暂无待处理任务'}<span>任务与提取记录</span></span><ChevronDown size={15} aria-hidden="true" /></summary>
        <div className="memory-task-content">
          {!!model.status.active_dreams && <p className="memory-dream-running-hint">正在后台执行 {model.status.active_dreams} 个记忆做梦整理任务。</p>}
          <div className="memory-progress">{repairJobs.map(job =>
            <button key={job.name} onClick={() => openPage(job.name)}>{job.name} · {labels[job.status] || job.status}<ArrowUpRight size={13} aria-hidden="true" /></button>)}</div>
          {!!model.status.extractions.length && <div className="memory-extraction-list">{model.status.extractions.map(item => <div className="memory-extraction" key={item.session_id}>
            <button onClick={() => onOpenSession(item.session_id)}>{item.session_id}<ArrowUpRight size={13} aria-hidden="true" /></button>
            <span>{labels[item.status || ''] || (item.pending_count ? '等待提取' : '已完成')} · 待处理 {item.pending_count || 0} 条 · 已完成 {item.total_extractions || 0} 批</span>
            {!!item.last_completed && <small>最近完成 {timeText(item.last_completed)}</small>}
            {item.error && <span className="memory-error-text">{item.error}</span>}
          </div>)}</div>}
          {!repairJobs.length && !model.status.extractions.length && !model.status.active_dreams && <p className="memory-muted">暂无任务或提取记录。</p>}
        </div>
      </details>}
      {model.report && <details className="memory-lint memory-disclosure" aria-label="记忆检查结果" open>
        <summary><span>检查结果 · {model.report.total_pages} 个页面 · {model.report.issues.length} 个问题</span><ChevronDown size={15} aria-hidden="true" /></summary>
        <div className="memory-lint-content">{model.report.issues.length ? model.report.issues.map((issue, index) => <div key={index}><button onClick={() => openPage(issue.page_name)}>{issue.page_name}<ArrowUpRight size={13} aria-hidden="true" /></button><span>{issue.detail}</span></div>) : <p><CheckCircle2 size={16} aria-hidden="true" />未发现规则检查问题。</p>}</div>
      </details>}
    </div>

    {model.dreamOpen && <section className="memory-modal-backdrop" role="dialog" aria-label="整理记忆（做梦）">
      <div className="memory-modal memory-dream-panel">
        <header className="memory-modal-header">
          <div>
            <h2><Sparkles size={18} aria-hidden="true" />整理记忆（做梦）</h2>
            <p className="memory-muted">在独立副本上核实、去重、合并并从会话记录提炼新认识；原记忆库在确认切换前保持不变。</p>
          </div>
          <button onClick={() => model.closeDreamPanel?.()} aria-label="关闭做梦面板"><X size={16} aria-hidden="true" />关闭</button>
        </header>

        <form className="memory-dream-create" onSubmit={event => {
          event.preventDefault()
          void model.startDream?.({ sessionIds: pickedSessions.length ? pickedSessions : null, instructions: dreamInstructions })
        }}>
          <label>
            整理方向说明（可选）
            <textarea
              className="memory-dream-instructions"
              rows={2}
              maxLength={4000}
              placeholder="例如：按项目拆分杂糅的页面；重点保留编码规范偏好，忽略临时调试报错。"
              value={dreamInstructions}
              onChange={event => setDreamInstructions(event.target.value)}
            />
          </label>
          {!!dreamSessions.length && <details className="memory-disclosure memory-dream-sessions">
            <summary>
              <span>{pickedSessions.length ? `已手动勾选 ${pickedSessions.length} 个主会话` : '默认自动选择上次整理以来有更新的主会话（最多 20 个）'}</span>
              <ChevronDown size={14} aria-hidden="true" />
            </summary>
            <div className="memory-dream-session-list">
              {dreamSessions.map(sess => <label key={sess.id} className="memory-dream-session-item">
                <input type="checkbox" checked={pickedSessions.includes(sess.id)} onChange={() => toggleSession(sess.id)} />
                <span><strong>{sess.title || sess.id}</strong><small>{sess.updated_at} · {sess.message_count} 条消息</small></span>
              </label>)}
            </div>
          </details>}
          <div className="memory-actions">
            <button
              type="submit"
              className="memory-primary-button memory-dream-submit"
              disabled={model.mutating || activeDream?.status === 'pending' || activeDream?.status === 'running'}
            >
              开始整理记忆
            </button>
          </div>
        </form>

        {!!dreams.length && <div className="memory-dream-selector">
          {dreams.map(item => <button
            key={item.id}
            className={`memory-dream-chip ${activeDream?.id === item.id ? 'selected' : ''}`}
            onClick={() => void model.selectDream?.(item.id)}
          >
            <span>{timeText(item.created_at)}</span>
            <small>{item.applied_at ? '已应用' : (labels[item.status] || item.status)} · {item.steps} 步</small>
          </button>)}
        </div>}

        {activeDream ? <div className="memory-dream-workspace">
          <div className="memory-dream-status-bar">
            <div className="memory-dream-metrics">
              <span className={`memory-state ${activeDream.status === 'completed' ? 'ready' : activeDream.status}`}>
                {activeDream.applied_at ? '已切换到此结果' : (labels[activeDream.status] || activeDream.status)}
              </span>
              <span>步数：{activeDream.steps}</span>
              <span>Token：{(activeDream.usage?.input_tokens || 0) + (activeDream.usage?.output_tokens || 0)}</span>
              <span>会话数：{activeDream.session_ids.length}</span>
              {activeDream.diff?.has_copy && <span>
                页面变化：{activeDream.diff.before_page_count} → {activeDream.diff.after_page_count} 页（+{activeDream.diff.added.length} / ~{activeDream.diff.modified.length} / -{activeDream.diff.removed.length}）
              </span>}
            </div>
            <div className="memory-actions">
              {(activeDream.status === 'pending' || activeDream.status === 'running') && <button
                className="memory-danger-button memory-dream-cancel"
                disabled={model.mutating}
                onClick={() => void model.cancelDream?.(activeDream.id)}
              >取消整理</button>}
              {['completed', 'canceled', 'failed'].includes(activeDream.status) && !activeDream.applied_at && !activeDream.archived_at && activeDream.diff?.has_copy && <>
                <button
                  className="memory-primary-button memory-dream-open-apply"
                  disabled={model.mutating}
                  onClick={() => { setCarryOver(true); void model.openApplyPreview?.(activeDream.id) }}
                >切换到新记忆</button>
                <button
                  className="memory-danger-button memory-dream-discard"
                  disabled={model.mutating}
                  onClick={() => void model.discardDream?.(activeDream.id)}
                >放弃并归档</button>
              </>}
            </div>
          </div>

          {activeDream.error && <div className="memory-notice memory-error" role="alert"><span>{activeDream.error}</span></div>}

          {model.applyPreview && model.applyPreview.dream_id === activeDream.id && <div className="memory-notice memory-dream-apply-confirm" role="region" aria-label="确认切换记忆库">
            <p><strong>确认切换到新整理的记忆库？</strong></p>
            <p className="memory-muted">{model.applyPreview.source_notice}</p>
            {model.applyPreview.warning && <p className="memory-warning-text">{model.applyPreview.warning}（{model.applyPreview.excluded_deleted.join('、')}）</p>}
            {(model.applyPreview.concurrent_modified.length > 0 || model.applyPreview.concurrent_added.length > 0) && <div className="memory-dream-concurrent">
              <p>整理期间原记忆库有以下改动：{
                [...model.applyPreview.concurrent_modified.map(i => `${i.name}（已更新）`),
                  ...model.applyPreview.concurrent_added.map(i => `${i.name}（新增）`)].join('、')
              }</p>
              <label className="memory-checkbox-label">
                <input type="checkbox" checked={carryOver} onChange={event => setCarryOver(event.target.checked)} />
                <span>带上整理期间原库的新增与修改页面（推荐，原样保留其来源和最新内容）</span>
              </label>
            </div>}
            <div className="memory-actions">
              <button className="memory-primary-button memory-dream-confirm-apply" disabled={model.mutating} onClick={() => void model.confirmApplyDream?.(carryOver)}>
                确认切换并归档原库
              </button>
              <button onClick={() => model.closeApplyPreview?.()}>暂不切换</button>
            </div>
          </div>}

          <div className="memory-dream-tabs" role="tablist">
            <button role="tab" aria-selected={dreamTab === 'diff'} className={dreamTab === 'diff' ? 'active' : ''} onClick={() => setDreamTab('diff')}>
              新库浏览与对比（{dreamPages.length}）
            </button>
            <button role="tab" aria-selected={dreamTab === 'transcript'} className={dreamTab === 'transcript' ? 'active' : ''} onClick={() => setDreamTab('transcript')}>
              整理过程回看（{model.dreamTranscript?.total ?? activeDream.steps}）
            </button>
          </div>

          {dreamTab === 'diff' ? <div className="memory-dream-diff-grid">
            <div className="memory-dream-page-list">
              {dreamPages.map(item => <button
                key={item.name}
                className={`memory-dream-page-item ${model.selectedDreamPage?.name === item.name ? 'selected' : ''}`}
                onClick={() => void model.inspectDreamPage?.(item.name)}
              >
                <strong>{item.name}</strong>
                <span>{item.description || '暂无摘要'}</span>
                <small><span className={`memory-badge ${item.change_type}`}>{labels[item.change_type] || item.change_type}</span> · {item.char_count} 字</small>
              </button>)}
              {!!activeDream.diff?.removed.length && <div className="memory-dream-removed-group">
                <h4>已合并或移除的旧页面（{activeDream.diff.removed.length}）</h4>
                {activeDream.diff.removed.map(item => <div key={item.name} className="memory-dream-removed-item">
                  <span>{item.name}</span><small>{item.char_count} 字</small>
                </div>)}
              </div>}
              {!dreamPages.length && <p className="memory-muted">副本中暂无页面。</p>}
            </div>
            <div className="memory-dream-page-detail">
              {model.selectedDreamPage ? <>
                <h3>{model.selectedDreamPage.name} <span className={`memory-badge ${model.selectedDreamPage.change_type}`}>{labels[model.selectedDreamPage.change_type]}</span></h3>
                <p className="memory-muted">{model.selectedDreamPage.description || '暂无摘要'}{model.selectedDreamPage.tags.length ? ` · 标签：${model.selectedDreamPage.tags.join('、')}` : ''}</p>
                {model.selectedDreamPage.diff && <details className="memory-disclosure" open>
                  <summary><span>前后差异对比（Diff）</span><ChevronDown size={14} aria-hidden="true" /></summary>
                  <pre className="memory-diff-pre">{model.selectedDreamPage.diff}</pre>
                </details>}
                <h4>新库正文</h4>
                <pre className="memory-diff-pre">{model.selectedDreamPage.content}</pre>
              </> : <p className="memory-muted">选择左侧任一新库页面查看正文与前后差异。</p>}
            </div>
          </div> : <div className="memory-dream-transcript">
            {model.dreamTranscript?.items.length ? model.dreamTranscript.items.map(step => <div key={step.step} className="memory-transcript-step">
              <div className="memory-transcript-head"><strong>第 {step.step} 步</strong><small>{timeText(step.at)} · 输入 {step.usage.input_tokens} / 输出 {step.usage.output_tokens} tokens</small></div>
              {step.content && <p>{step.content}</p>}
              {step.tool_calls.map((tc, index) => <details key={tc.id || index} className="memory-disclosure memory-transcript-tool">
                <summary><span>调用 <code>{tc.name}</code></span><ChevronDown size={14} aria-hidden="true" /></summary>
                <pre>{tc.arguments}</pre>
                {step.tool_results[index] && <pre>{step.tool_results[index].result}</pre>}
              </details>)}
            </div>) : <p className="memory-muted">暂无过程记录。</p>}
          </div>}
        </div> : <p className="memory-muted">尚未发起记忆整理任务。填写方向说明或直接点击「开始整理记忆」创建第一份整理副本。</p>}
      </div>
    </section>}

    {model.storeVersionsOpen && <section className="memory-modal-backdrop" role="dialog" aria-label="整库版本回退">
      <div className="memory-modal memory-store-versions-panel">
        <header className="memory-modal-header">
          <div>
            <h2><History size={18} aria-hidden="true" />整库归档版本</h2>
            <p className="memory-muted">每次切换记忆做梦结果或整库回退前都会自动归档整库快照（保留 30 天且至少最近 5 份）。</p>
          </div>
          <button onClick={() => model.closeStoreVersions?.()} aria-label="关闭库版本面板"><X size={16} aria-hidden="true" />关闭</button>
        </header>
        <div className="memory-store-version-list">
          {storeVersions.length ? storeVersions.map(ver => <div key={ver.id} className="memory-store-version-item">
            <div>
              <strong>{ver.id}</strong>
              <p>{ver.reason || '整库快照'} · {ver.page_count} 个页面 · {ver.total_chars} 字</p>
              <small>{timeText(ver.created_at)}</small>
            </div>
            <div className="memory-actions">
              {confirmStoreId === ver.id ? <>
                <button className="memory-primary-button memory-confirm-store-restore" disabled={model.mutating} onClick={() => { setConfirmStoreId(''); void model.restoreStoreVersion?.(ver.id) }}>
                  确认回退到此版本
                </button>
                <button onClick={() => setConfirmStoreId('')}>取消</button>
              </> : <button disabled={model.mutating} onClick={() => setConfirmStoreId(ver.id)}>
                <RotateCcw size={14} aria-hidden="true" />回退到此版本
              </button>}
            </div>
          </div>) : <p className="memory-muted">暂无整库归档版本。</p>}
        </div>
      </div>
    </section>}

    <div className="memory-layout" data-active-pane={activePane}>
      <section className="memory-list" aria-label="记忆列表" ref={listRef}>
        <div className="memory-list-heading"><h2>全部记忆</h2><span>{model.pages.total}</span></div>
        <label className="memory-search"><span className="sr-only">搜索记忆</span><Search size={16} aria-hidden="true" />
          <input value={model.query} onChange={event => model.search(event.target.value)} placeholder="名称、标签或正文" />
        </label>
        <div className="memory-list-count" role="status">{model.loading ? '正在读取…' : model.query ? `找到 ${model.pages.total} 条匹配记忆` : '偏好、项目知识与经验'}</div>
        <div className="memory-list-items">
          {model.pages.items.map(item => <button className={`memory-list-item ${model.selected === item.name ? 'selected' : ''}`} key={item.name} onClick={() => openPage(item.name)} aria-current={model.selected === item.name ? 'page' : undefined}>
            <span className="memory-list-title"><FileText size={16} aria-hidden="true" /><strong>{item.name}</strong></span>
            <span className="memory-list-description">{item.description || '暂无摘要'}</span>
            {!!item.tags.length && <span className="memory-list-tags">{item.tags.join(' · ')}</span>}
            <span className="memory-list-meta"><span className={`memory-state ${item.status}`}>{labels[item.status]}</span><time dateTime={item.updated_at}>{timeText(item.updated_at)}</time></span>
          </button>)}
          {!model.pages.items.length && !model.loading && <div className="memory-list-empty"><BookOpen size={24} strokeWidth={1.5} aria-hidden="true" /><p>暂无匹配记忆</p><span>可以在聊天中告诉 Agent 需要记住的内容。</span></div>}
        </div>
        <nav className="memory-pagination" aria-label="记忆分页">
          <button disabled={model.offset === 0} onClick={() => model.setOffset(Math.max(0, model.offset - 20))}><ChevronLeft size={15} aria-hidden="true" />上一页</button>
          <span>{Math.floor(model.offset / 20) + 1}</span>
          <button disabled={model.offset + 20 >= model.pages.total} onClick={() => model.setOffset(model.offset + 20)}>下一页<ChevronRight size={15} aria-hidden="true" /></button>
        </nav>
      </section>
      <section className="memory-detail" aria-label="记忆详情">
        <button className="memory-back" ref={backRef} onClick={showList}><ChevronLeft size={16} aria-hidden="true" />返回记忆列表</button>
        {page ? <>
          <header className="memory-detail-header">
            <div className="memory-document-heading"><h2>{page.name}</h2>
              <div className="memory-document-meta"><span className={`memory-state ${page.status}`}>{labels[page.status]}</span><span>版本 {page.revision}</span><span>更新于 {timeText(page.updated_at)}</span></div>
            </div>
            <div className="memory-actions">
              <button disabled={model.mutating || !!draft} onClick={model.startEdit}><Pencil size={14} aria-hidden="true" />编辑</button>
              <button className="memory-danger-button" disabled={model.mutating} onClick={() => setConfirmDelete(page.name)}><Trash2 size={14} aria-hidden="true" />删除</button>
              {['failed', 'pending', 'inactive'].includes(page.status) && <button disabled={model.mutating} onClick={() => void model.repair()}><RotateCcw size={14} aria-hidden="true" />重新整理</button>}
            </div>
          </header>
          {confirmDelete === page.name && <div className="memory-notice memory-delete-confirm" role="alert">
            <p>删除“{page.name}”？后台旧任务将无法恢复这条记忆。</p>
            <div className="memory-actions"><button className="memory-danger-button" disabled={model.mutating} onClick={() => { setConfirmDelete(''); void model.remove() }}>确认删除</button><button onClick={() => setConfirmDelete('')}>取消</button></div>
          </div>}
          {draft ? <form className="memory-editor" onSubmit={event => { event.preventDefault(); void model.save() }}>
            <label>摘要<input value={draft.description} onChange={event => model.updateDraft({ description: event.target.value })} /></label>
            <div className="memory-editor-properties">
              <label>标签<span className="memory-field-hint">中文或英文逗号分隔</span><input aria-label="标签（中文或英文逗号分隔）" value={draft.tagsText} onChange={event => model.updateDraft({ tagsText: event.target.value })} /></label>
              <label>关联页面<span className="memory-field-hint">中文或英文逗号分隔</span><input aria-label="关联页面（中文或英文逗号分隔）" value={draft.relatedText} onChange={event => model.updateDraft({ relatedText: event.target.value })} /></label>
            </div>
            <label>正文<textarea value={draft.content} onChange={event => model.updateDraft({ content: event.target.value })} rows={15} /></label>
            <p className="memory-editor-note">保存后作为你确认的内容，自动整理会尊重本次修改。</p>
            {model.conflict && <div className="memory-notice memory-conflict"><p>草稿已保留。请查看最新内容，再决定是否用当前草稿更新。</p>
              <details className="memory-disclosure"><summary><span>最新正文（版本 {page.revision}）</span><ChevronDown size={15} aria-hidden="true" /></summary><pre>{page.content}</pre></details>
              <button type="button" onClick={() => model.updateDraft({ revision: page.revision })}>以此版本继续保存草稿</button>
            </div>}
            <div className="memory-actions memory-editor-actions"><button className="memory-primary-button" type="submit" disabled={model.mutating || !draft.content.trim()}>{model.mutating ? '保存中…' : '保存'}</button>
              <button type="button" disabled={model.mutating} onClick={() => model.cancelEdit()}>取消编辑</button></div>
          </form> : <div className="markdown-body memory-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ href, children }) =>
            href?.startsWith('#wiki-page=') ? <button className="memory-link" onClick={() => openPage(decodeURIComponent(href.slice(11)))}>{children}</button>
              : <a href={href} rel="noreferrer">{children}</a> }}>{content || '该页面没有可用正文。'}</ReactMarkdown></div>}
          <section className="memory-relations"><h3><Link2 size={16} aria-hidden="true" />关联引用</h3>
            <div className="memory-relation-links">{[...new Set([...page.links, ...page.backlinks])].map(name => <button key={name} onClick={() => openPage(name)}>{name}{page.backlinks.includes(name) ? ' · 引用本页' : ''}<ArrowUpRight size={13} aria-hidden="true" /></button>)}
              {!page.links.length && !page.backlinks.length && <span className="memory-muted">暂无关联</span>}
            </div>
          </section>
          <section className="memory-sources"><h3>来源<span>{page.sources.length}</span></h3>
            {!page.sources.length && <p className="memory-muted">历史来源未追溯，无法自动还原原始聊天依据；如删除聊天后仍残留旧内容，可手动编辑或回退版本。</p>}
            {page.legacy_baseline && <p className="memory-muted">本页还包含来源未追溯的历史基线。</p>}
            {page.sources.map(source => <details className="memory-disclosure memory-source" key={source.id}>
              <summary><span><strong>{source.kind === 'manual' ? '你的页面修改' : source.role === 'user' ? '用户消息' : '助手背景'}</strong><small>{source.state === 'valid' ? '有效' : '已撤销或缺失'} · {timeText(source.captured_at)}</small></span><ChevronDown size={15} aria-hidden="true" /></summary>
              <div className="memory-source-body"><code>{source.id}</code>{source.body ? <pre>{source.body}</pre> : <p>来源原文已删除。</p>}
                {source.diff && <><h4>修改差异</h4><pre>{source.diff}</pre></>}
                {source.state === 'valid' && source.session_id && source.kind === 'chat' && <button onClick={() => onOpenSession(source.session_id!)}>查看原会话<ArrowUpRight size={14} aria-hidden="true" /></button>}
              </div>
            </details>)}
          </section>
          <section className="memory-history">
            <details className="memory-disclosure" onToggle={event => {
              if ((event.currentTarget as HTMLDetailsElement).open && !historyItems.length) void model.loadPageHistory?.(page.name)
            }}>
              <summary>
                <h3><History size={16} aria-hidden="true" />历史版本与回滚<span>{historyItems.length ? `${historyItems.length}` : ''}</span></h3>
                <ChevronDown size={15} aria-hidden="true" />
              </summary>
              <div className="memory-history-body">
                <div className="memory-actions">
                  <button className="memory-load-history" disabled={model.historyLoading} onClick={() => void model.loadPageHistory?.(page.name)}>
                    <RefreshCw size={13} aria-hidden="true" />{model.historyLoading ? '读取中…' : '刷新历史版本'}
                  </button>
                </div>
                {model.historyPreview && <div className="memory-history-preview">
                  <div className="memory-history-preview-head">
                    <strong>历史快照 r{model.historyPreview.revision}（{timeText(model.historyPreview.updated_at)}）</strong>
                    <button onClick={() => model.closeHistoryPreview?.()}>收起预览</button>
                  </div>
                  <pre>{model.historyPreview.content}</pre>
                </div>}
                {historyItems.length ? historyItems.map(item => <div key={item.revision} className="memory-history-item">
                  <div>
                    <strong>版本 r{item.revision}</strong>
                    <span> · {timeText(item.updated_at)} · {item.char_count} 字</span>
                    {item.description && <p className="memory-muted">{item.description}</p>}
                  </div>
                  <div className="memory-actions">
                    <button onClick={() => void model.previewHistoryVersion?.(item.revision)}>查看内容</button>
                    {confirmHistoryRev === item.revision ? <>
                      <button className="memory-primary-button memory-confirm-history-restore" disabled={model.mutating} onClick={() => { setConfirmHistoryRev(null); void model.restorePageVersion?.(item.revision) }}>
                        确认回滚
                      </button>
                      <button onClick={() => setConfirmHistoryRev(null)}>取消</button>
                    </> : <button className="memory-trigger-history-restore" disabled={model.mutating} onClick={() => setConfirmHistoryRev(item.revision)}>
                      <RotateCcw size={13} aria-hidden="true" />回滚到此版本
                    </button>}
                  </div>
                </div>) : <p className="memory-muted">{model.historyLoading ? '正在读取历史版本…' : '暂无历史版本（页面被修改或覆盖后会自动留存旧版本）。'}</p>}
              </div>
            </details>
          </section>
          {page.repair && <section className="memory-repair"><h3>整理记录</h3><p>{labels[page.repair.status] || page.repair.status}{page.repair.reason ? ` · ${page.repair.reason}` : ''}</p>
            {page.repair.error && <p className="memory-error-text" role="alert">{page.repair.error}</p>}
            {page.repair.records?.map((record, index) => <p key={index}>{timeText(record.at)} · {labels[record.status] || record.status}{record.revision ? ` · 版本 ${record.revision}` : ''}{record.error ? ` · ${record.error}` : ''}</p>)}
          </section>}
        </> : <div className="memory-detail-empty"><FileText size={36} strokeWidth={1.25} aria-hidden="true" /><h2>{model.loading ? '正在读取记忆…' : model.selected ? '此页面不存在' : '知识，从一次对话开始'}</h2><p>{model.loading ? '正在载入页面正文与来源。' : model.selected ? '请选择其他记忆。' : '选择一条记忆，查看正文、关联与来源。'}</p></div>}
      </section>
    </div>
  </>
}
