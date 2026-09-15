import type { Conversation } from '../../models/workbench'

type ConversationListProps = {
  conversations: Conversation[]
  activeId: string | null
  disabled: boolean
  onSelect: (id: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
}

/** 格式化会话更新时间。 */
function formatUpdatedAt(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}

/** 可切换、重命名和删除的会话列表。 */
export function ConversationList({ conversations, activeId, disabled, onSelect, onRename, onDelete }: ConversationListProps) {
  if (!conversations.length) return <div className="empty-compact">还没有会话</div>
  return (
    <div className="conversation-list">
      {conversations.map((conversation) => (
        <div key={conversation.id} className={`conversation-item ${conversation.id === activeId ? 'active' : ''}`}>
          <button className="conversation-main" type="button" disabled={disabled} onClick={() => onSelect(conversation.id)}>
            <span className="conversation-title">{conversation.title}</span>
            <span className="conversation-meta">{formatUpdatedAt(conversation.updatedAt)}</span>
          </button>
          {conversation.id === activeId && <div className="conversation-actions">
            <button type="button" onClick={() => { const title = window.prompt('输入新的会话名称', conversation.title); if (title) onRename(conversation.id, title) }}>改名</button>
            <button type="button" onClick={() => { if (window.confirm(`确认删除会话“${conversation.title}”？不会删除工作区文件。`)) onDelete(conversation.id) }}>删除</button>
          </div>}
        </div>
      ))}
    </div>
  )
}
