import { useRef, useState } from 'react'
import { ArrowUpRight, BookOpen, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, Clock3, FileText, Link2, Pencil, RefreshCw, RotateCcw, ScanLine, Search, Trash2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useMemoryWorkspace } from './useMemoryWorkspace'
import type { createApiClient } from '../../lib/api'
import './memory.css'

const labels: Record<string, string> = { legacy: '来源未追溯', ready: '可用', pending: '等待整理', processing: '整理中', failed: '整理失败', inactive: '来源已撤销', completed: '已完成', idle: '暂无待提取正文' }
/** 统一展示来源时间和页面更新时间。 */
const timeText = (value?: number | string) => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN') : '暂无'

/** 将 Wiki 链接转为页面入口；渲染仍使用 Markdown 的安全链接处理。 */
function linkedMarkdown(content: string) {
  return content.replace(/\[\[([^\]]+)\]\]/g, (_whole, link: string) => {
    const [target, alias] = link.split('|', 2)
    const name = target.split('#', 1)[0].trim()
    return name ? `[${(alias || target).replace(/[[\]]/g, '')}](#wiki-page=${encodeURIComponent(name)})` : target
  })
}

/** 记忆工作区入口：ViewModel 保存逻辑，面板只负责交互展示。 */
export function MemoryWorkspace({ api, visible, activityKey, onOpenSession }: {
  api: ReturnType<typeof createApiClient>; visible: boolean; activityKey: number; onOpenSession: (id: string) => void
}) {
  const model = useMemoryWorkspace(api, visible, activityKey)
  return <div hidden={!visible} className="memory-workspace" role="main" aria-label="记忆管理"><MemoryPanel model={model} onOpenSession={onOpenSession} /></div>
}

/** 记忆列表、详情、来源和编辑表单，所有业务操作委托 ViewModel。 */
export function MemoryPanel({ model, onOpenSession }: {
  model: ReturnType<typeof useMemoryWorkspace>; onOpenSession: (id: string) => void
}) {
  const [confirmDelete, setConfirmDelete] = useState('')
  const [activePane, setActivePane] = useState<'list' | 'detail'>('list')
  const listRef = useRef<HTMLElement>(null)
  const backRef = useRef<HTMLButtonElement>(null)
  const page = model.detail
  const draft = model.draft
  const content = page ? linkedMarkdown(page.content) : ''
  const repairJobs = model.status?.repairs.filter(job => ['pending', 'processing', 'failed'].includes(job.status)) || []

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

  return <>
    <header className="memory-header">
      <h1>记忆库</h1>
      <div className="memory-actions">
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
          <div className="memory-progress">{repairJobs.map(job =>
            <button key={job.name} onClick={() => openPage(job.name)}>{job.name} · {labels[job.status] || job.status}<ArrowUpRight size={13} aria-hidden="true" /></button>)}</div>
          {!!model.status.extractions.length && <div className="memory-extraction-list">{model.status.extractions.map(item => <div className="memory-extraction" key={item.session_id}>
            <button onClick={() => onOpenSession(item.session_id)}>{item.session_id}<ArrowUpRight size={13} aria-hidden="true" /></button>
            <span>{labels[item.status || ''] || (item.pending_count ? '等待提取' : '已完成')} · 待处理 {item.pending_count || 0} 条 · 已完成 {item.total_extractions || 0} 批</span>
            {!!item.last_completed && <small>最近完成 {timeText(item.last_completed)}</small>}
            {item.error && <span className="memory-error-text">{item.error}</span>}
          </div>)}</div>}
          {!repairJobs.length && !model.status.extractions.length && <p className="memory-muted">暂无任务或提取记录。</p>}
        </div>
      </details>}
      {model.report && <details className="memory-lint memory-disclosure" aria-label="记忆检查结果" open>
        <summary><span>检查结果 · {model.report.total_pages} 个页面 · {model.report.issues.length} 个问题</span><ChevronDown size={15} aria-hidden="true" /></summary>
        <div className="memory-lint-content">{model.report.issues.length ? model.report.issues.map((issue, index) => <div key={index}><button onClick={() => openPage(issue.page_name)}>{issue.page_name}<ArrowUpRight size={13} aria-hidden="true" /></button><span>{issue.detail}</span></div>) : <p><CheckCircle2 size={16} aria-hidden="true" />未发现规则检查问题。</p>}</div>
      </details>}
    </div>
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
            {!page.sources.length && <p className="memory-muted">历史来源未追溯，无法自动还原原始聊天依据。</p>}
            {page.legacy_baseline && <p className="memory-muted">本页还包含来源未追溯的历史基线。</p>}
            {page.sources.map(source => <details className="memory-disclosure memory-source" key={source.id}>
              <summary><span><strong>{source.kind === 'manual' ? '你的页面修改' : source.role === 'user' ? '用户消息' : '助手背景'}</strong><small>{source.state === 'valid' ? '有效' : '已撤销或缺失'} · {timeText(source.captured_at)}</small></span><ChevronDown size={15} aria-hidden="true" /></summary>
              <div className="memory-source-body"><code>{source.id}</code>{source.body ? <pre>{source.body}</pre> : <p>来源原文已删除。</p>}
                {source.diff && <><h4>修改差异</h4><pre>{source.diff}</pre></>}
                {source.state === 'valid' && source.session_id && source.kind === 'chat' && <button onClick={() => onOpenSession(source.session_id!)}>查看原会话<ArrowUpRight size={14} aria-hidden="true" /></button>}
              </div>
            </details>)}
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
