import { useCallback, useRef, useState } from 'react'

/**
 * 一组会话 ID：ref 供异步回调立即读取最新值（不必等重渲染），state 驱动视图更新。
 * 加载中、运行中、操作中三组状态共用这一写法，避免每次修改都要同时维护两份。
 */
export function useIdSet() {
  const ref = useRef(new Set<string>())
  const [ids, setIds] = useState<Set<string>>(() => new Set())
  const publish = useCallback(() => setIds(new Set(ref.current)), [])
  const add = useCallback((id: string) => { ref.current.add(id); publish() }, [publish])
  const remove = useCallback((id: string) => { ref.current.delete(id); publish() }, [publish])
  const replace = useCallback((next: Iterable<string>) => { ref.current = new Set(next); publish() }, [publish])
  return { ref, ids, has: (id: string) => ref.current.has(id), add, remove, replace, publish }
}
