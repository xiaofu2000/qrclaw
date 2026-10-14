import { useEffect, useId, useState } from 'react'
import type { AgentActivity } from '../lib/types'
import { NOTIF_BLUE } from '../vendor/bloub/decor'
import { BotEngine, type BotFrame } from '../vendor/bloub/engine'
import { RAYON, DEMI_VIEWBOX } from '../vendor/bloub/repere'
import { mixHex } from '../vendor/bloub/skins'
import { POSES, STATE_BY_ID, type StateId } from '../vendor/bloub/states'

/** 只描述阶段的视觉表现，不在组件中判断任务是否完成。 */
const SPRITE_STATES: Record<AgentActivity['phase'], StateId> = {
  idle: 'idle', loading: 'thinking', queued: 'sleep', thinking: 'thinking',
  tool: 'orbit', subagent: 'burst', responding: 'wide', compressing: 'comet',
  completed: 'notify', error: 'alert', background: 'orbit',
}

/** 渲染上游粒子，保留路径形状和远近颜色，避免简化后丢失原动画细节。 */
function SpriteDots({ dots }: { dots: BotFrame['dots'] }) {
  return <g>{dots.map((dot, index) => {
    const fill = dot.color ?? (dot.depth === undefined ? '#0a0a0c' : mixHex('#ffffff', '#0a0a0c', dot.depth))
    return dot.d
      ? <path key={index} d={dot.d} fill={fill} opacity={dot.opacity}
          transform={`translate(${dot.x} ${dot.y}) rotate(${dot.rot ?? 0}) scale(${RAYON})`} />
      : <circle key={index} cx={dot.x} cy={dot.y} r={dot.r} fill={fill} opacity={dot.opacity} />
  })}</g>
}

/** 将 bloub 原生引擎适配为 React 视图；隐藏页面或减少动态效果时停止逐帧刷新。 */
export function AgentSprite({ phase }: { phase: AgentActivity['phase'] }) {
  const state = SPRITE_STATES[phase]
  const uid = useId()
  const maskId = `${uid}-mask`
  const [engine] = useState(() => new BotEngine(RAYON))
  const [frame, setFrame] = useState(() => engine.sample(0))

  useEffect(() => {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let raf = 0
    let start = performance.now() / 1000
    let paintedAt = -Infinity
    const duration = STATE_BY_ID.get(state)!.duration
    const repeats = state === 'orbit' || state === 'burst' || state === 'comet'
    engine.setState(state, start)

    /** 按真实时间采样，限制到每秒 30 帧，单次工作动画之间留出回到原形的过渡。 */
    function tick(milliseconds: number) {
      const now = milliseconds / 1000
      if (now - paintedAt >= 1 / 30) {
        const target = repeats && (now - start) % (duration + 1) >= duration ? 'idle' : state
        engine.setState(target, now)
        setFrame(engine.sample(now))
        paintedAt = now
      }
      raf = requestAnimationFrame(tick)
    }

    /** 动态响应系统偏好和页面可见性，静态模式仍展示当前阶段的代表帧。 */
    function resume() {
      cancelAnimationFrame(raf)
      if (motion.matches) {
        setFrame(new BotEngine(RAYON, state).sample(POSES[state]))
      } else if (!document.hidden) {
        start = performance.now() / 1000
        engine.setState(state, start)
        raf = requestAnimationFrame(tick)
      }
    }
    motion.addEventListener('change', resume)
    document.addEventListener('visibilitychange', resume)
    resume()
    return () => {
      cancelAnimationFrame(raf)
      motion.removeEventListener('change', resume)
      document.removeEventListener('visibilitychange', resume)
    }
  }, [engine, state])

  return (
    <svg className="agent-sprite" viewBox={`${-DEMI_VIEWBOX} ${-DEMI_VIEWBOX} ${DEMI_VIEWBOX * 2} ${DEMI_VIEWBOX * 2}`}
      data-animation={state} aria-hidden="true" focusable="false">
      <defs>
        <mask id={maskId} maskUnits="userSpaceOnUse" x={-DEMI_VIEWBOX} y={-DEMI_VIEWBOX} width={DEMI_VIEWBOX * 2} height={DEMI_VIEWBOX * 2}>
          <path d={frame.bodyPath} fill="#fff" />
          {frame.eyes.map((eye, index) => <path key={index} d={eye.d} transform={eye.matrix} opacity={eye.alpha} fill="#000" />)}
          {frame.notch && <circle cx={frame.notch.x} cy={frame.notch.y} r={frame.notch.r} fill="#000" />}
        </mask>
        {frame.arcs.map(arc => <linearGradient key={arc.id} id={`${uid}-${arc.id}`} gradientUnits="userSpaceOnUse"
          x1={arc.grad.x1} y1={arc.grad.y1} x2={arc.grad.x2} y2={arc.grad.y2}>
          {arc.grad.stops.map((color, index) => <stop key={index} offset={index / (arc.grad.stops.length - 1)} stopColor={color} />)}
        </linearGradient>)}
      </defs>
      <g fill="none" strokeLinecap="round">
        {frame.arcs.map(arc => <path key={arc.id} d={arc.back} stroke={`url(#${uid}-${arc.id})`} strokeWidth={arc.width} opacity={arc.opacity} />)}
      </g>
      {frame.dotsBehind && <SpriteDots dots={frame.dots} />}
      <g opacity={frame.bodyAlpha}>
        <path d={frame.bodyPath} fill="#fff" />
        <path d={frame.bodyPath} fill="#0a0a0c" mask={`url(#${maskId})`} />
      </g>
      {!frame.dotsBehind && <SpriteDots dots={frame.dots} />}
      {frame.notif && <circle cx={frame.notif.x} cy={frame.notif.y} r={frame.notif.r} fill={NOTIF_BLUE} />}
      <g fill="none" strokeLinecap="round">
        {frame.arcs.map(arc => <path key={arc.id} d={arc.front} stroke={`url(#${uid}-${arc.id})`} strokeWidth={arc.width} opacity={arc.opacity} />)}
      </g>
    </svg>
  )
}
