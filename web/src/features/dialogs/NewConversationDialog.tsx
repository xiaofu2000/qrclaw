import { useEffect, useState, type FormEvent } from 'react'
import type { DirectoryListing } from '../../models/workbench'
import type { CreateConversationInput } from '../../viewmodels/useWorkbenchViewModel'
import { DirectoryPicker } from './DirectoryPicker'

type NewConversationDialogProps = {
  defaultWorkspace: string
  pending: boolean
  onBrowseWorkspace: (path?: string) => Promise<DirectoryListing>
  onClose: () => void
  onSubmit: (input: CreateConversationInput) => void
}

/** 收集新会话名称，并通过目录选择器确定本地工作区。 */
export function NewConversationDialog({ defaultWorkspace, pending, onBrowseWorkspace, onClose, onSubmit }: NewConversationDialogProps) {
  const [title, setTitle] = useState('新任务')
  const [workspacePath, setWorkspacePath] = useState(defaultWorkspace)
  const [pickerOpen, setPickerOpen] = useState(false)
  useEffect(() => setWorkspacePath(defaultWorkspace), [defaultWorkspace])

  /** 校验表单后创建任务会话。 */
  function submit(event: FormEvent) {
    event.preventDefault()
    if (title.trim() && workspacePath) {
      onSubmit({ title: title.trim(), workspacePath })
    }
  }

  return (
    <div className="dialog-backdrop">
      <form className="form-dialog new-conversation-dialog" onSubmit={submit}>
        <header><h2>{pickerOpen ? '选择工作区' : '新建任务会话'}</h2><button type="button" aria-label="关闭" onClick={onClose}>×</button></header>
        {pickerOpen ? (
          <DirectoryPicker
            initialPath={workspacePath || defaultWorkspace}
            onBrowse={onBrowseWorkspace}
            onCancel={() => setPickerOpen(false)}
            onSelect={(path) => {
              setWorkspacePath(path)
              setPickerOpen(false)
            }}
          />
        ) : (
          <>
            <label>会话名称<input value={title} onChange={(event) => setTitle(event.target.value)} autoFocus /></label>
            <div className="form-field">
              <span>工作区</span>
              <div className="workspace-picker-field">
                <div className={workspacePath ? '' : 'placeholder'} title={workspacePath}>{workspacePath || '尚未选择工作区'}</div>
                <button type="button" onClick={() => setPickerOpen(true)}>选择文件夹</button>
              </div>
            </div>
            <p className="form-help">只保存所选目录的路径；删除会话不会删除工作区文件。</p>
            <footer><button type="button" onClick={onClose}>取消</button><button className="primary-button" type="submit" disabled={pending || !title.trim() || !workspacePath}>创建</button></footer>
          </>
        )}
      </form>
    </div>
  )
}
