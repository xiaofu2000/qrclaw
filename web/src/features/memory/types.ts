/** Wiki 摘要与来源状态，与后端持久化字段对应。 */
export type WikiSummary = {
  name: string; description: string; tags: string[]; related: string[]; revision: number
  status: 'legacy' | 'ready' | 'pending' | 'processing' | 'failed' | 'inactive'
  created_at: string; updated_at: string; source_count: number
}
/** 来源撤销后没有 body，页面只能展示撤销标记。 */
export type WikiSource = {
  id: string; state: string; role?: string; kind?: string; session_id?: string | null
  message_id?: string | null; captured_at?: number; body?: string; diff?: string
}
export type WikiRepair = {
  name: string; status: string; reason?: string; error?: string; queued_at?: number; finished_at?: number
  records?: { status: string; at: number; revision?: number; error?: string }[]
}
export type WikiDetail = WikiSummary & {
  content: string; sources: WikiSource[]; links: string[]; backlinks: string[]; repair: WikiRepair | null; legacy_baseline?: boolean
}
export type WikiPageList = { items: WikiSummary[]; total: number; offset: number; limit: number }
export type WikiStatus = {
  pending: number; repairs: WikiRepair[]
  extractions: { session_id: string; pending_count?: number; total_extractions?: number; last_attempt?: number; last_completed?: number; status?: string; error?: string }[]
}
export type WikiLintReport = {
  total_pages: number; issues: { issue_type: string; page_name: string; detail: string; severity: string }[]
}
/** 编辑草稿保留打开时的版本，后台刷新不会改写这个版本。 */
export type WikiDraft = Pick<WikiDetail, 'content' | 'description' | 'tags' | 'related' | 'revision'>
/** 保留输入中的分隔符，避免用户输入逗号时被即时格式化吞掉。 */
export type WikiEditState = WikiDraft & { tagsText: string; relatedText: string }
