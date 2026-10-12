import { useCallback, useEffect, useRef, useState } from 'react'
import type { createApiClient } from '../../lib/api'
import type { WikiDetail, WikiEditState, WikiLintReport, WikiPageList, WikiStatus } from './types'

type MemoryApi = Pick<ReturnType<typeof createApiClient>, 'listWikiPages' | 'getWikiPage' | 'getWikiStatus' | 'editWikiPage' | 'deleteWikiPage' | 'lintWiki' | 'repairWikiPage'>
const emptyList: WikiPageList = { items: [], total: 0, offset: 0, limit: 20 }

/** 记忆 ViewModel：管理草稿、分页、冲突和轮询，组件不直接访问网络。 */
export function useMemoryWorkspace(api: MemoryApi, visible: boolean, activityKey = 0) {
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [pages, setPages] = useState(emptyList)
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<WikiDetail | null>(null)
  const [status, setStatus] = useState<WikiStatus | null>(null)
  const [drafts, setDrafts] = useState<Record<string, WikiEditState>>({})
  const [report, setReport] = useState<WikiLintReport | null>(null)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const [loading, setLoading] = useState(false)
  const [mutating, setMutating] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const epoch = useRef(0)
  const selectedRef = useRef(selected)
  selectedRef.current = selected
  const busy = useRef(false)
  const refresh = useCallback(() => setRefreshKey(value => value + 1), [])

  useEffect(() => {
    if (!visible) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    /** 只查询状态，后台存在任务时续查；响应按请求代次隔离。 */
    async function load() {
      if (disposed || document.hidden) return
      if (timer) clearTimeout(timer)
      controller?.abort()
      controller = new AbortController()
      const current = ++epoch.current
      setLoading(true)
      const results = await Promise.allSettled([
        api.listWikiPages(query, offset, controller.signal), api.getWikiStatus(controller.signal),
        selected ? api.getWikiPage(selected, controller.signal) : Promise.resolve(null),
      ])
      if (disposed || current !== epoch.current || document.hidden) return
      const [listResult, statusResult, detailResult] = results
      if (listResult.status === 'fulfilled') {
        setPages(listResult.value)
        if (offset > 0 && !listResult.value.items.length) setOffset(Math.max(0, Math.floor((listResult.value.total - 1) / 20) * 20))
        if (!selected && listResult.value.items[0]) setSelected(listResult.value.items[0].name)
      }
      if (statusResult.status === 'fulfilled') setStatus(statusResult.value)
      if (detailResult.status === 'fulfilled') setDetail(detailResult.value)
      else if ((detailResult.reason as { status?: number })?.status === 404) setDetail(null)
      const failure = results.find(result => result.status === 'rejected')
      if (failure?.status === 'rejected' && failure.reason?.name !== 'AbortError') setError(String(failure.reason?.message ?? failure.reason))
      setLoading(false)
      if (statusResult.status === 'fulfilled' && statusResult.value.pending > 0) timer = setTimeout(() => void load(), 2000)
    }
    /** 后台标签页暂停请求，返回可见时只重新查询。 */
    function visibilityChanged() {
      if (document.hidden) {
        if (timer) clearTimeout(timer)
        controller?.abort()
        epoch.current++
      } else void load()
    }
    document.addEventListener('visibilitychange', visibilityChanged)
    void load()
    return () => {
      disposed = true
      if (timer) clearTimeout(timer)
      controller?.abort()
      document.removeEventListener('visibilitychange', visibilityChanged)
    }
  }, [api, visible, query, offset, selected, refreshKey, activityKey])

  /** 切换页面保留各自的编辑草稿，旧详情不能覆盖新的选择。 */
  function selectPage(name: string) {
    epoch.current++
    selectedRef.current = name
    setSelected(name)
    setDetail(null)
    setError('')
    setConflict(false)
  }
  /** 新搜索从第一页开始。 */
  function search(value: string) { setQuery(value); setOffset(0) }
  /** 草稿固定打开时版本，不随后台刷新被覆盖。 */
  function startEdit() {
    if (!detail) return
    const { content, description, tags, related, revision } = detail
    setDrafts(value => ({ ...value, [detail.name]: { content, description, tags: [...tags], related: [...related], revision,
      tagsText: tags.join(', '), relatedText: related.join(', ') } }))
  }
  /** 输入原文与解析后的标签、关联集合同时保留。 */
  function updateDraft(change: Partial<WikiEditState>) {
    if (change.tagsText !== undefined) change.tags = change.tagsText.split(/[,，]/).map(value => value.trim()).filter(Boolean)
    if (change.relatedText !== undefined) change.related = change.relatedText.split(/[,，]/).map(value => value.trim()).filter(Boolean)
    setDrafts(value => value[selected] ? { ...value, [selected]: { ...value[selected], ...change } } : value)
  }
  /** 仅清除指定页面草稿，不影响其他页面。 */
  function cancelEdit(name = selected) {
    setDrafts(value => { const next = { ...value }; delete next[name]; return next })
    setConflict(false)
  }

  /** 写入只执行一次；轮询、失败和断线均不会自动重发。 */
  async function mutate(action: () => Promise<void>, name = selected) {
    if (busy.current) return
    busy.current = true
    epoch.current++
    setMutating(true)
    setError('')
    setConflict(false)
    try { await action() }
    catch (failure) {
      if (selectedRef.current === name) {
        const problem = failure as Error & { status?: number }
        setConflict(problem.status === 409)
        setError(problem.message || '操作失败，请重新读取记忆状态')
      }
    } finally {
      epoch.current++
      busy.current = false
      setMutating(false)
      refresh()
    }
  }
  /** 提交一次草稿，成功后读取后端保存结果。 */
  function save() {
    const name = selected, draft = drafts[name]
    if (!draft) return Promise.resolve()
    return mutate(async () => {
      const saved = await api.editWikiPage(name, draft)
      cancelEdit(name)
      if (selectedRef.current === name) setDetail(saved)
    }, name)
  }
  /** 删除当前版本；晚到的响应不能清空后来选中的页面。 */
  function remove() {
    const page = detail
    if (!page) return Promise.resolve()
    return mutate(async () => {
      await api.deleteWikiPage(page.name, page.revision)
      cancelEdit(page.name)
      if (selectedRef.current === page.name) { setSelected(''); setDetail(null) }
    }, page.name)
  }
  /** 将当前页面加入持久化整理队列。 */
  function repair() {
    const name = selected
    return mutate(async () => {
      const queued = await api.repairWikiPage(name)
      if (selectedRef.current === name) setDetail(queued)
    }, name)
  }
  /** 用户明确发起检查，后台轮询不会重复执行。 */
  function lint() { return mutate(async () => setReport(await api.lintWiki())) }

  return { query, search, offset, setOffset, pages, selected, selectPage, detail, status, draft: drafts[selected],
    startEdit, updateDraft, cancelEdit, save, remove, repair, lint, report, error, conflict, loading, mutating, refresh }
}
