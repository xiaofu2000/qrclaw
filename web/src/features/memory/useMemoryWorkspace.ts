import { useCallback, useEffect, useRef, useState } from 'react'
import type { createApiClient } from '../../lib/api'
import type { SessionSummary } from '../../lib/types'
import type {
  WikiDetail, WikiDream, WikiDreamApplyPreview, WikiDreamPageDetail, WikiDreamPageSummary,
  WikiDreamTranscript, WikiEditState, WikiHistoryDetail, WikiHistoryItem, WikiLintReport,
  WikiPageList, WikiStatus, WikiStoreVersion,
} from './types'

type FullApi = ReturnType<typeof createApiClient>
type RequiredMemoryApi = Pick<FullApi, 'listWikiPages' | 'getWikiPage' | 'getWikiStatus' | 'editWikiPage' | 'deleteWikiPage' | 'lintWiki' | 'repairWikiPage'>
type OptionalMemoryApi = Partial<Pick<FullApi,
  'listPageHistory' | 'getPageHistoryVersion' | 'restoreWikiPage' |
  'listStoreVersions' | 'restoreStoreVersion' |
  'createWikiDream' | 'listWikiDreams' | 'getWikiDream' | 'listWikiDreamPages' |
  'getWikiDreamPage' | 'getWikiDreamTranscript' | 'cancelWikiDream' |
  'previewApplyWikiDream' | 'applyWikiDream' | 'archiveWikiDream' | 'listSessions'
>>
export type MemoryApi = RequiredMemoryApi & OptionalMemoryApi

const emptyList: WikiPageList = { items: [], total: 0, offset: 0, limit: 20 }

/** 记忆 ViewModel：管理草稿、分页、冲突、单页历史、整库版本、做梦整理和轮询，组件不直接访问网络。 */
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

  // 单页历史版本状态
  const [pageHistory, setPageHistory] = useState<WikiHistoryItem[]>([])
  const [historyPreview, setHistoryPreview] = useState<WikiHistoryDetail | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)

  // 整库归档版本状态
  const [storeVersionsOpen, setStoreVersionsOpen] = useState(false)
  const [storeVersions, setStoreVersions] = useState<WikiStoreVersion[]>([])

  // 记忆做梦状态
  const [dreamOpen, setDreamOpen] = useState(false)
  const [dreams, setDreams] = useState<WikiDream[]>([])
  const [activeDream, setActiveDream] = useState<WikiDream | null>(null)
  const [dreamPages, setDreamPages] = useState<WikiDreamPageSummary[]>([])
  const [selectedDreamPage, setSelectedDreamPage] = useState<WikiDreamPageDetail | null>(null)
  const [dreamTranscript, setDreamTranscript] = useState<WikiDreamTranscript | null>(null)
  const [applyPreview, setApplyPreview] = useState<WikiDreamApplyPreview | null>(null)
  const [dreamSessions, setDreamSessions] = useState<SessionSummary[]>([])

  const epoch = useRef(0)
  const selectedRef = useRef(selected)
  selectedRef.current = selected
  const activeDreamIdRef = useRef('')
  activeDreamIdRef.current = activeDream?.id ?? ''
  const dreamOpenRef = useRef(dreamOpen)
  dreamOpenRef.current = dreamOpen
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

      // 做梦抽屉打开且存在正在整理的任务时，随轮询同步最新进度、副本页面和过程记录
      const dreamId = activeDreamIdRef.current
      if (dreamOpenRef.current && dreamId && api.getWikiDream) {
        const [dRes, pRes, tRes] = await Promise.allSettled([
          api.getWikiDream(dreamId, controller.signal),
          api.listWikiDreamPages ? api.listWikiDreamPages(dreamId, '', controller.signal) : Promise.resolve(null),
          api.getWikiDreamTranscript ? api.getWikiDreamTranscript(dreamId, 0, 50, controller.signal) : Promise.resolve(null),
        ])
        if (!disposed && current === epoch.current && !document.hidden) {
          if (dRes.status === 'fulfilled') {
            setActiveDream(dRes.value)
            setDreams(items => items.map(item => item.id === dRes.value.id ? dRes.value : item))
          }
          if (pRes.status === 'fulfilled' && pRes.value) setDreamPages(pRes.value.items)
          if (tRes.status === 'fulfilled' && tRes.value) setDreamTranscript(tRes.value)
        }
      }

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
    if (selectedRef.current === name) return
    epoch.current++
    selectedRef.current = name
    setSelected(name)
    setDetail(null)
    setPageHistory([])
    setHistoryPreview(null)
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
      if (selectedRef.current === page.name) { setSelected(''); setDetail(null); setPageHistory([]); setHistoryPreview(null) }
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

  // ── 单页历史版本与回滚 ──────────────────────────────────────────────────

  async function loadPageHistory(name = selected) {
    if (!name || !api.listPageHistory) return
    setHistoryLoading(true)
    try {
      const res = await api.listPageHistory(name)
      if (selectedRef.current === name) setPageHistory(res.items)
    } catch (failure) {
      setError(String((failure as Error)?.message ?? failure))
    } finally {
      setHistoryLoading(false)
    }
  }

  async function previewHistoryVersion(revision: number) {
    const name = selected
    if (!name || !api.getPageHistoryVersion) return
    try {
      const version = await api.getPageHistoryVersion(name, revision)
      if (selectedRef.current === name) setHistoryPreview(version)
    } catch (failure) {
      setError(String((failure as Error)?.message ?? failure))
    }
  }

  function restorePageVersion(revision: number) {
    const name = selected, curRev = detail?.revision
    if (!name || !api.restoreWikiPage) return Promise.resolve()
    return mutate(async () => {
      const restored = await api.restoreWikiPage!(name, revision, curRev)
      cancelEdit(name)
      if (selectedRef.current === name) {
        setDetail(restored)
        setHistoryPreview(null)
        if (api.listPageHistory) setPageHistory((await api.listPageHistory(name)).items)
      }
    }, name)
  }

  // ── 整库版本与回退 ──────────────────────────────────────────────────────

  async function openStoreVersions() {
    setStoreVersionsOpen(true)
    if (!api.listStoreVersions) return
    try {
      const res = await api.listStoreVersions()
      setStoreVersions(res.items)
    } catch (failure) {
      setError(String((failure as Error)?.message ?? failure))
    }
  }

  function closeStoreVersions() { setStoreVersionsOpen(false) }

  function restoreStoreVersion(versionId: string) {
    if (!api.restoreStoreVersion) return Promise.resolve()
    return mutate(async () => {
      await api.restoreStoreVersion!(versionId)
      if (api.listStoreVersions) setStoreVersions((await api.listStoreVersions()).items)
      setStoreVersionsOpen(false)
      setSelected('')
      setDetail(null)
    }, selected)
  }

  // ── 记忆做梦（整理） ────────────────────────────────────────────────────

  async function selectDream(dreamId: string) {
    if (!api.getWikiDream) return
    activeDreamIdRef.current = dreamId
    setSelectedDreamPage(null)
    setApplyPreview(null)
    try {
      const [dream, pagesRes, transRes] = await Promise.all([
        api.getWikiDream(dreamId),
        api.listWikiDreamPages ? api.listWikiDreamPages(dreamId) : Promise.resolve({ items: [], total: 0 }),
        api.getWikiDreamTranscript ? api.getWikiDreamTranscript(dreamId, 0, 50) : Promise.resolve(null),
      ])
      setActiveDream(dream)
      setDreamPages(pagesRes.items)
      setDreamTranscript(transRes)
    } catch (failure) {
      setError(String((failure as Error)?.message ?? failure))
    }
  }

  async function openDreamPanel() {
    setDreamOpen(true)
    dreamOpenRef.current = true
    try {
      const [dreamsRes, sessionsRes] = await Promise.all([
        api.listWikiDreams ? api.listWikiDreams(20, false) : Promise.resolve({ items: [], total: 0 }),
        api.listSessions ? api.listSessions() : Promise.resolve([]),
      ])
      setDreams(dreamsRes.items)
      setDreamSessions(sessionsRes)
      if (dreamsRes.items[0]) await selectDream(dreamsRes.items[0].id)
      else { setActiveDream(null); setDreamPages([]); setDreamTranscript(null) }
    } catch (failure) {
      setError(String((failure as Error)?.message ?? failure))
    }
  }

  function closeDreamPanel() {
    setDreamOpen(false)
    dreamOpenRef.current = false
    setApplyPreview(null)
  }

  function startDream(options: { sessionIds?: string[] | null; instructions?: string } = {}) {
    if (!api.createWikiDream) return Promise.resolve()
    return mutate(async () => {
      const created = await api.createWikiDream!({
        ...(options.sessionIds && options.sessionIds.length ? { session_ids: options.sessionIds } : {}),
        instructions: options.instructions ?? '',
      })
      setDreams(items => [created, ...items.filter(item => item.id !== created.id)])
      await selectDream(created.id)
    }, selected)
  }

  function cancelDream(dreamId = activeDream?.id) {
    if (!dreamId || !api.cancelWikiDream) return Promise.resolve()
    return mutate(async () => {
      const canceled = await api.cancelWikiDream!(dreamId)
      setActiveDream(canceled)
      setDreams(items => items.map(item => item.id === canceled.id ? canceled : item))
    }, selected)
  }

  async function inspectDreamPage(name: string) {
    const dreamId = activeDream?.id
    if (!dreamId || !api.getWikiDreamPage) return
    try {
      setSelectedDreamPage(await api.getWikiDreamPage(dreamId, name))
    } catch (failure) {
      setError(String((failure as Error)?.message ?? failure))
    }
  }

  async function openApplyPreview(dreamId = activeDream?.id) {
    if (!dreamId || !api.previewApplyWikiDream) return
    try {
      setApplyPreview(await api.previewApplyWikiDream(dreamId))
    } catch (failure) {
      setError(String((failure as Error)?.message ?? failure))
    }
  }

  function confirmApplyDream(carryOver = true) {
    const dreamId = activeDream?.id
    if (!dreamId || !api.applyWikiDream) return Promise.resolve()
    return mutate(async () => {
      await api.applyWikiDream!(dreamId, carryOver)
      setApplyPreview(null)
      setSelected('')
      setDetail(null)
      if (api.getWikiDream) {
        const updated = await api.getWikiDream(dreamId)
        setActiveDream(updated)
        setDreams(items => items.map(item => item.id === updated.id ? updated : item))
      }
    }, selected)
  }

  function discardDream(dreamId = activeDream?.id) {
    if (!dreamId || !api.archiveWikiDream) return Promise.resolve()
    return mutate(async () => {
      await api.archiveWikiDream!(dreamId)
      const remaining = dreams.filter(item => item.id !== dreamId)
      setDreams(remaining)
      setApplyPreview(null)
      setSelectedDreamPage(null)
      if (remaining[0]) await selectDream(remaining[0].id)
      else { setActiveDream(null); setDreamPages([]); setDreamTranscript(null) }
    }, selected)
  }

  return {
    query, search, offset, setOffset, pages, selected, selectPage, detail, status, draft: drafts[selected],
    startEdit, updateDraft, cancelEdit, save, remove, repair, lint, report, error, conflict, loading, mutating, refresh,
    pageHistory, historyPreview, historyLoading, loadPageHistory, previewHistoryVersion, closeHistoryPreview: () => setHistoryPreview(null), restorePageVersion,
    storeVersionsOpen, storeVersions, openStoreVersions, closeStoreVersions, restoreStoreVersion,
    dreamOpen, dreams, activeDream, dreamPages, selectedDreamPage, dreamTranscript, applyPreview, dreamSessions,
    openDreamPanel, closeDreamPanel, selectDream, startDream, cancelDream, inspectDreamPage,
    openApplyPreview, closeApplyPreview: () => setApplyPreview(null), confirmApplyDream, discardDream,
  }
}
