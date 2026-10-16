import { useLayoutEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { AgentActivity } from '../lib/types'
import { AgentSprite } from './AgentSprite'

const SIZE = 104
const INSET = 16

/** 窗口缩小时仍保留整张卡片，避免拖出屏幕后无法找回。 */
function constrain(x: number, y: number) {
  return { x: Math.max(INSET, Math.min(x, window.innerWidth - SIZE - INSET)),
    y: Math.max(INSET, Math.min(y, window.innerHeight - SIZE - INSET)) }
}

/** 相对工作面定位，聊天左上角不会覆盖侧栏品牌与导航。 */
function defaultPosition(corner: 'left' | 'right', surfaceRef: RefObject<HTMLDivElement | null>) {
  const surface = surfaceRef.current?.getBoundingClientRect()
  const x = corner === 'left' ? (surface?.left ?? 0) + INSET
    : (surface?.right ?? window.innerWidth - INSET) - SIZE - INSET
  return constrain(x, INSET)
}

/** 桌面伙伴按当前页面停靠顶部，拖动与键盘移动只改变视图位置。 */
export function FloatingCompanion({ activity, sessionId, corner, surfaceRef }: {
  activity: AgentActivity; sessionId: string; corner: 'left' | 'right'; surfaceRef: RefObject<HTMLDivElement | null>
}) {
  const [position, setPosition] = useState(() => defaultPosition(corner, surfaceRef))
  const moved = useRef(false)
  const drag = useRef<{ id: number; x: number; y: number } | null>(null)

  useLayoutEffect(() => {
    // 切换聊天与记忆时恢复各自默认位置，不重建动画实例。
    moved.current = false
    drag.current = null
    setPosition(defaultPosition(corner, surfaceRef))
    function resize() {
      setPosition(previous => moved.current ? constrain(previous.x, previous.y)
        : defaultPosition(corner, surfaceRef))
    }
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [corner, surfaceRef])

  return <div className="floating-companion" role="group" tabIndex={0} aria-label="小精灵：拖动或用方向键移动，Home 键回到顶部"
    title="拖动调整位置 · 方向键移动 · Home 回到顶部" data-phase={activity.phase} data-corner={corner}
    style={{ left: position.x, top: position.y, width: SIZE, height: SIZE }}
    onPointerDown={event => {
      if (event.button !== 0) return
      drag.current = { id: event.pointerId, x: event.clientX - position.x, y: event.clientY - position.y }
      event.currentTarget.setPointerCapture(event.pointerId)
    }}
    onPointerMove={event => {
      if (drag.current?.id !== event.pointerId) return
      moved.current = true
      setPosition(constrain(event.clientX - drag.current.x, event.clientY - drag.current.y))
    }}
    onPointerUp={event => {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
      drag.current = null
    }}
    onPointerCancel={() => { drag.current = null }} onLostPointerCapture={() => { drag.current = null }}
    onKeyDown={event => {
      const directions: Record<string, [number, number]> = { ArrowLeft: [-16, 0], ArrowRight: [16, 0], ArrowUp: [0, -16], ArrowDown: [0, 16] }
      if (event.key === 'Home') {
        event.preventDefault()
        moved.current = false
        setPosition(defaultPosition(corner, surfaceRef))
      } else if (directions[event.key]) {
        event.preventDefault()
        moved.current = true
        const [x, y] = directions[event.key]
        setPosition(constrain(position.x + x, position.y + y))
      }
    }}>
    <AgentSprite key={sessionId} phase={activity.phase} />
  </div>
}
