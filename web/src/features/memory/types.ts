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
  pending: number; repairs: WikiRepair[]; active_dreams?: number
  extractions: { session_id: string; pending_count?: number; total_extractions?: number; last_attempt?: number; last_completed?: number; status?: string; error?: string }[]
}
export type WikiLintReport = {
  total_pages: number; issues: { issue_type: string; page_name: string; detail: string; severity: string }[]
}
/** 编辑草稿保留打开时的版本，后台刷新不会改写这个版本。 */
export type WikiDraft = Pick<WikiDetail, 'content' | 'description' | 'tags' | 'related' | 'revision'>
/** 保留输入中的分隔符，避免用户输入逗号时被即时格式化吞掉。 */
export type WikiEditState = WikiDraft & { tagsText: string; relatedText: string }

/** 单页不可变历史版本条目。 */
export type WikiHistoryItem = {
  name: string; revision: number; description: string; tags: string[]; related: string[]
  status: string; source_count: number; char_count: number; created_at: string; updated_at: string
}
export type WikiHistoryDetail = WikiHistoryItem & { content: string }
export type WikiHistoryList = { name: string; items: WikiHistoryItem[]; total: number }

/** 整库归档版本快照。 */
export type WikiStoreVersion = {
  id: string; created_at: number; reason: string; dream_id?: string | null; page_count: number; total_chars: number
}
export type WikiStoreVersionList = { items: WikiStoreVersion[]; total: number }

/** 记忆做梦（整理）模型与对比数据结构。 */
export type WikiDreamDiff = {
  has_copy: boolean
  added: { name: string; description: string; tags: string[]; char_count: number }[]
  removed: { name: string; description: string; tags: string[]; char_count: number }[]
  modified: { name: string; description: string; tags: string[]; before_chars: number; after_chars: number }[]
  unchanged_count: number
  before_page_count: number; after_page_count: number
  before_total_chars: number; after_total_chars: number
}
export type WikiDream = {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'canceled'
  session_ids: string[]
  instructions: string
  usage: { input_tokens: number; output_tokens: number; total_tokens?: number }
  steps: number
  error: string | null
  created_at: number
  ended_at: number | null
  applied_at: number | null
  archived_at: number | null
  archived_store_version?: string | null
  diff?: WikiDreamDiff
}
export type WikiDreamList = { items: WikiDream[]; total: number }
export type WikiDreamPageSummary = {
  name: string; description: string; tags: string[]; related: string[]
  char_count: number; change_type: 'added' | 'modified' | 'unchanged'
}
export type WikiDreamPageList = { items: WikiDreamPageSummary[]; total: number }
export type WikiDreamPageDetail = WikiDreamPageSummary & {
  content: string
  original: { content: string; description: string; tags: string[]; related: string[]; revision: number } | null
  diff: string
}
export type WikiDreamStep = {
  step: number; at: number; content: string
  tool_calls: { id: string; name: string; arguments: string }[]
  tool_results: { tool_call_id: string; name: string; result: string }[]
  usage: { input_tokens: number; output_tokens: number; total_tokens?: number }
}
export type WikiDreamTranscript = {
  dream_id: string; total: number; offset: number; limit: number; items: WikiDreamStep[]
}
export type WikiDreamApplyPreview = {
  dream_id: string; status: string; can_apply: boolean
  concurrent_modified: { name: string; base_revision: number; current_revision: number; description: string }[]
  concurrent_added: { name: string; revision: number; description: string }[]
  excluded_deleted: string[]
  warning: string
  source_notice: string
}
