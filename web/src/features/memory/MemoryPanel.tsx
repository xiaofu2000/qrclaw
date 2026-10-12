import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useMemoryWorkspace } from './useMemoryWorkspace'
import type { createApiClient } from '../../lib/api'

const labels: Record<string, string> = { legacy: '来源未追溯', ready: '可用', pending: '等待整理', processing: '整理中', failed: '整理失败', inactive: '来源已撤销', completed: '已完成', idle: '暂无待提取正文' }
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
  const page = model.detail
  const draft = model.draft
  const content = page ? linkedMarkdown(page.content) : ''
  return <>
    <header className="memory-header"><div><h1>记忆</h1><p>跨会话保存的偏好、项目知识与经验</p></div>
      <div className="memory-actions"><button onClick={model.refresh} disabled={model.mutating}>刷新</button>
        <button onClick={() => void model.lint()} disabled={model.mutating}>检查记忆</button></div></header>
    {model.error && <div className="chat-error" role="alert"><span>{model.error}</span><button onClick={model.refresh}>重新载入</button></div>}
    {model.status && <div className="memory-progress" role="status">{model.status.pending ? `${model.status.pending} 项记忆任务等待或正在处理` : '记忆已同步'}
      {model.status.repairs.filter(job => ['pending', 'processing', 'failed'].includes(job.status)).map(job =>
        <button key={job.name} onClick={() => model.selectPage(job.name)}>{job.name} · {labels[job.status] || job.status}</button>)}</div>}
    {!!model.status?.extractions.length && <details className="memory-extractions"><summary>会话提取进度</summary>{model.status.extractions.map(item => <p key={item.session_id}>
      <button onClick={() => onOpenSession(item.session_id)}>{item.session_id}</button> · {labels[item.status || ''] || (item.pending_count ? '等待提取' : '已完成')} · 待处理 {item.pending_count || 0} 条 · 已完成 {item.total_extractions || 0} 批
      {item.last_completed ? ` · 最近完成 ${timeText(item.last_completed)}` : ''}{item.error ? ` · ${item.error}` : ''}</p>)}</details>}
    <div className="memory-layout">
      <section className="memory-list" aria-label="记忆列表"><label className="memory-search">搜索记忆<input value={model.query} onChange={event => model.search(event.target.value)} placeholder="页面名称、标签或正文" /></label>
        <div className="memory-list-count">共 {model.pages.total} 条{model.loading ? ' · 正在读取…' : ''}</div>
        {model.pages.items.map(item => <button className={`memory-list-item ${model.selected === item.name ? 'selected' : ''}`} key={item.name} onClick={() => model.selectPage(item.name)}>
          <strong>{item.name}</strong><span>{item.description || '暂无摘要'}</span>
          <small>{item.tags.join(' · ')}</small><small>{labels[item.status]} · {timeText(item.updated_at)}</small></button>)}
        {!model.pages.items.length && !model.loading && <p className="muted">暂无匹配记忆。可以在聊天中告诉 Agent 需要记住的内容。</p>}
        <nav className="memory-pagination" aria-label="记忆分页"><button disabled={model.offset === 0} onClick={() => model.setOffset(Math.max(0, model.offset - 20))}>上一页</button>
          <span>{Math.floor(model.offset / 20) + 1}</span><button disabled={model.offset + 20 >= model.pages.total} onClick={() => model.setOffset(model.offset + 20)}>下一页</button></nav>
      </section>
      <section className="memory-detail" aria-label="记忆详情">
        {page ? <>
          <div className="memory-detail-header"><div><h2>{page.name}</h2><p>{labels[page.status]} · 版本 {page.revision} · 更新于 {timeText(page.updated_at)}</p></div>
            <div className="memory-actions"><button disabled={model.mutating || !!draft} onClick={model.startEdit}>编辑</button>
              <button disabled={model.mutating} onClick={() => setConfirmDelete(page.name)}>删除</button>
              {['failed', 'pending', 'inactive'].includes(page.status) && <button disabled={model.mutating} onClick={() => void model.repair()}>重新整理</button>}</div></div>
          {confirmDelete === page.name && <div className="memory-delete-confirm" role="alert">删除“{page.name}”？后台旧任务将无法恢复这条记忆。
            <button disabled={model.mutating} onClick={() => { setConfirmDelete(''); void model.remove() }}>确认删除</button><button onClick={() => setConfirmDelete('')}>取消</button></div>}
          {draft ? <form className="memory-editor" onSubmit={event => { event.preventDefault(); void model.save() }}>
            <label>摘要<input value={draft.description} onChange={event => model.updateDraft({ description: event.target.value })} /></label>
            <label>标签（中文或英文逗号分隔）<input value={draft.tagsText} onChange={event => model.updateDraft({ tagsText: event.target.value })} /></label>
            <label>关联页面（中文或英文逗号分隔）<input value={draft.relatedText} onChange={event => model.updateDraft({ relatedText: event.target.value })} /></label>
            <label>正文<textarea value={draft.content} onChange={event => model.updateDraft({ content: event.target.value })} rows={15} /></label>
            <p className="muted">保存后作为你确认的内容，自动整理会尊重本次修改。</p>
            {model.conflict && <div className="memory-conflict"><p>草稿已保留。请查看最新内容，再决定是否用当前草稿更新。</p>
              <details><summary>最新正文（版本 {page.revision}）</summary><pre>{page.content}</pre></details>
              <button type="button" onClick={() => model.updateDraft({ revision: page.revision })}>以此版本继续保存草稿</button></div>}
            <div className="memory-actions"><button type="submit" disabled={model.mutating || !draft.content.trim()}>{model.mutating ? '保存中…' : '保存'}</button>
              <button type="button" disabled={model.mutating} onClick={() => model.cancelEdit()}>取消编辑</button></div>
          </form> : <div className="markdown-body memory-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ href, children }) =>
            href?.startsWith('#wiki-page=') ? <button className="memory-link" onClick={() => model.selectPage(decodeURIComponent(href.slice(11)))}>{children}</button>
              : <a href={href} rel="noreferrer">{children}</a> }}>{content || '该页面没有可用正文。'}</ReactMarkdown></div>}
          <section className="memory-relations"><h3>关联引用</h3>{[...new Set([...page.links, ...page.backlinks])].map(name => <button key={name} onClick={() => model.selectPage(name)}>{name}{page.backlinks.includes(name) ? ' · 引用本页' : ''}</button>)}
            {!page.links.length && !page.backlinks.length && <span className="muted">暂无关联</span>}</section>
          <section className="memory-sources"><h3>来源 · {page.sources.length}</h3>{!page.sources.length && <p className="muted">历史来源未追溯，无法自动还原原始聊天依据。</p>}
            {page.legacy_baseline && <p className="muted">本页还包含来源未追溯的历史基线。</p>}
            {page.sources.map(source => <details key={source.id}><summary>{source.kind === 'manual' ? '你的页面修改' : source.role === 'user' ? '用户消息' : '助手背景'} · {source.state === 'valid' ? '有效' : '已撤销或缺失'} · {timeText(source.captured_at)}</summary>
              <code>{source.id}</code>{source.body ? <pre>{source.body}</pre> : <p>来源原文已删除。</p>}
              {source.diff && <><h4>修改差异</h4><pre>{source.diff}</pre></>}
              {source.state === 'valid' && source.session_id && source.kind === 'chat' && <button onClick={() => onOpenSession(source.session_id!)}>查看原会话</button>}</details>)}</section>
          {page.repair && <section className="memory-repair"><h3>整理记录</h3><p>{labels[page.repair.status] || page.repair.status} · {page.repair.reason}</p>
            {page.repair.error && <p role="alert">{page.repair.error}</p>}{page.repair.records?.map((record, index) => <p key={index}>{timeText(record.at)} · {labels[record.status] || record.status}{record.revision ? ` · 版本 ${record.revision}` : ''}{record.error ? ` · ${record.error}` : ''}</p>)}</section>}
        </> : <p className="muted">{model.loading ? '正在读取记忆…' : model.selected ? '此页面不存在，请选择其他记忆。' : '选择一条记忆查看正文和来源。'}</p>}
      </section>
    </div>
    {model.report && <section className="memory-lint" aria-label="记忆检查结果"><h2>检查结果 · {model.report.total_pages} 个页面</h2>
      {model.report.issues.length ? model.report.issues.map((issue, index) => <div key={index}><button onClick={() => model.selectPage(issue.page_name)}>{issue.page_name}</button><span>{issue.detail}</span></div>) : <p>未发现规则检查问题。</p>}</section>}
  </>
}
