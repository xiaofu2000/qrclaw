import { useEffect, useState, type FormEvent } from 'react'
import type { CreateConversationInput } from '../../viewmodels/useWorkbenchViewModel'

type NewConversationDialogProps = { defaultWorkspace: string; pending: boolean; onClose: () => void; onSubmit: (input: CreateConversationInput) => void }

/** 收集新会话名称和本地工作区。 */
export function NewConversationDialog({ defaultWorkspace, pending, onClose, onSubmit }: NewConversationDialogProps) {
  const [title, setTitle] = useState('新任务')
  const [workspacePath, setWorkspacePath] = useState(defaultWorkspace)
  useEffect(() => setWorkspacePath(defaultWorkspace), [defaultWorkspace])
  function submit(event: FormEvent) { event.preventDefault(); if (title.trim() && workspacePath.trim()) onSubmit({ title: title.trim(), workspacePath: workspacePath.trim() }) }
  return <div className="dialog-backdrop"><form className="form-dialog" onSubmit={submit}><header><h2>新建任务会话</h2><button type="button" onClick={onClose}>×</button></header><label>会话名称<input value={title} onChange={(event) => setTitle(event.target.value)} autoFocus /></label><label>工作区绝对路径<input value={workspacePath} onChange={(event) => setWorkspacePath(event.target.value)} placeholder="/Users/name/project" /></label><p className="form-help">后端会校验目录是否存在，不会因为删除会话而删除工作区文件。</p><footer><button type="button" onClick={onClose}>取消</button><button className="primary-button" type="submit" disabled={pending || !title.trim() || !workspacePath.trim()}>创建</button></footer></form></div>
}

