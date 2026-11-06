import { useState } from 'react'
import { Check, CircleCheck, CircleDashed, CircleDot, LoaderCircle, Pencil, Trash2, X } from 'lucide-react'
import type { Conversation } from '../../lib/types'

/** 会话状态图标：运行中、未读回复、空白和已读各一种形状，并附读屏文字。 */
export function SessionState({ running, unread, empty, label }: { running: boolean; unread: boolean; empty: boolean; label?: string }) {
  if (running) return <LoaderCircle size={16} className="session-state running spin" aria-label={label ?? '任务处理中'} />
  if (unread) return <CircleDot size={16} className="session-state unread" role="img" aria-label="有未读回复" />
  if (empty) return <CircleDashed size={16} className="session-state" aria-hidden="true" />
  return <CircleCheck size={16} className="session-state" aria-hidden="true" />
}

type ConversationListProps = {
  conversations: Conversation[]
  activeId: string
  runningIds: Set<string>
  unreadIds: Set<string>
  mutatingIds: Set<string>
  onSelect: (conversationId: string) => void
  onRename: (conversationId: string, title: string) => void
  onDelete: (conversationId: string) => void
}

/** 会话列表只管理改名输入和删除确认，数据操作交给工作区。 */
export function ConversationList({ conversations, activeId, runningIds, unreadIds, mutatingIds, onSelect, onRename, onDelete }: ConversationListProps) {
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
              <SessionState running={runningIds.has(conversation.id) || mutatingIds.has(conversation.id)} unread={unreadIds.has(conversation.id)}
                empty={!conversation.messageCount} label={mutatingIds.has(conversation.id) ? '更新会话中' : undefined} />
              <span className="conversation-title">{conversation.title}</span>
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
