import { useState } from 'react'
import { Check, LoaderCircle, Pencil, Trash2, X } from 'lucide-react'
import type { Conversation } from '../../lib/types'

type ConversationListProps = {
  conversations: Conversation[]
  activeId: string
  runningIds: Set<string>
  mutatingIds: Set<string>
  onSelect: (conversationId: string) => void
  onRename: (conversationId: string, title: string) => void
  onDelete: (conversationId: string) => void
}

/** 会话列表只管理改名输入和删除确认，数据操作交给工作区。 */
export function ConversationList({ conversations, activeId, runningIds, mutatingIds, onSelect, onRename, onDelete }: ConversationListProps) {
  const [editingId, setEditingId] = useState('')
  const [title, setTitle] = useState('')

  function saveTitle() {
    if (!title.trim() || !editingId || mutatingIds.has(editingId)) return
    onRename(editingId, title.trim())
    setEditingId('')
  }

  return (
    <div className="conversation-list">
      {conversations.map((conversation) => (
        <div key={conversation.id} className={`conversation-row ${conversation.id === activeId ? 'active' : ''}`}>
          {editingId === conversation.id ? <>
            <input className="conversation-rename" aria-label="会话名称" autoFocus value={title} maxLength={80}
              onChange={event => setTitle(event.target.value)} onKeyDown={event => {
                if (event.key === 'Enter') saveTitle()
                if (event.key === 'Escape') setEditingId('')
              }} />
            <button className="conversation-action" type="button" aria-label="保存会话名称" disabled={!title.trim() || mutatingIds.has(conversation.id)} onClick={saveTitle}><Check size={15} /></button>
            <button className="conversation-action" type="button" aria-label="取消改名" onClick={() => setEditingId('')}><X size={15} /></button>
          </> : <>
            <button type="button" title={conversation.title} aria-current={conversation.id === activeId ? 'page' : undefined}
              className="conversation-item" onClick={() => onSelect(conversation.id)}>
              <span className="conversation-title">{conversation.title}</span>
              {runningIds.has(conversation.id) && <LoaderCircle size={15} className="spin" aria-label="任务处理中" />}
              {mutatingIds.has(conversation.id) && <LoaderCircle size={15} className="spin" aria-label="更新会话中" />}
            </button>
            <button className="conversation-action" type="button" aria-label={`重命名会话 ${conversation.title}`}
              disabled={mutatingIds.has(conversation.id)}
              onClick={() => { setEditingId(conversation.id); setTitle(conversation.title) }}><Pencil size={14} /></button>
            <button className="conversation-action danger" type="button" aria-label={`删除会话 ${conversation.title}`}
              disabled={runningIds.has(conversation.id) || mutatingIds.has(conversation.id)} onClick={() => {
                if (window.confirm(`确定删除会话“${conversation.title}”及其中的全部消息吗？此操作无法撤销。`)) onDelete(conversation.id)
              }}><Trash2 size={14} /></button>
          </>}
        </div>
      ))}
    </div>
  )
}
