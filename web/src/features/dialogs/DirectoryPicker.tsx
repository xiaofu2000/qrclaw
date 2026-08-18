import { useCallback, useEffect, useState } from 'react'
import type { DirectoryListing } from '../../models/workbench'

type DirectoryPickerProps = {
  initialPath: string
  onBrowse: (path?: string) => Promise<DirectoryListing>
  onCancel: () => void
  onSelect: (path: string) => void
}

/** 应用内本地目录选择器，避免要求用户手工输入绝对路径。 */
export function DirectoryPicker({ initialPath, onBrowse, onCancel, onSelect }: DirectoryPickerProps) {
  const [listing, setListing] = useState<DirectoryListing | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showHidden, setShowHidden] = useState(false)
  const visibleDirectories = listing?.directories.filter(
    (directory) => showHidden || !directory.name.startsWith('.'),
  ) ?? []

  const load = useCallback(async (path = '') => {
    setLoading(true)
    setError(null)
    try {
      setListing(await onBrowse(path))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取目录失败')
    } finally {
      setLoading(false)
    }
  }, [onBrowse])

  useEffect(() => {
    void load(initialPath)
  }, [initialPath, load])

  return (
    <section className="directory-picker" aria-label="选择工作区">
      <div className="directory-picker-toolbar">
        <button type="button" disabled={!listing?.parentPath || loading} onClick={() => void load(listing?.parentPath ?? '')}>上一级</button>
        <button type="button" disabled={loading} onClick={() => void load(listing?.homePath ?? '')}>个人目录</button>
        {initialPath && <button type="button" disabled={loading} onClick={() => void load(initialPath)}>默认目录</button>}
        <label className="directory-hidden-toggle"><input type="checkbox" checked={showHidden} onChange={(event) => setShowHidden(event.target.checked)} />显示隐藏目录</label>
      </div>
      <div className="directory-current" title={listing?.currentPath}>{listing?.currentPath ?? '正在读取目录…'}</div>
      {error && <div className="directory-error" role="alert">{error}</div>}
      <div className="directory-list" aria-busy={loading}>
        {!loading && visibleDirectories.map((directory) => (
          <button key={directory.path} type="button" onClick={() => void load(directory.path)}>
            <span aria-hidden="true">▸</span>
            <span>{directory.name}</span>
          </button>
        ))}
        {!loading && visibleDirectories.length === 0 && <div className="directory-empty">这个目录没有可显示的子文件夹</div>}
      </div>
      <footer>
        <button type="button" onClick={onCancel}>返回</button>
        <button className="primary-button" type="button" disabled={!listing || loading} onClick={() => listing && onSelect(listing.currentPath)}>选择此文件夹</button>
      </footer>
    </section>
  )
}
