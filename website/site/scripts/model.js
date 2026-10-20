import { scenes, phases, memories, entries } from './content.js';

/** 保存官网交互状态，视图与动效只订阅状态变化。 */
export class WebsiteModel extends EventTarget {
  constructor(reducedMotion = false) {
    super();
    this.state = { phase: 0, progress: 0, scene: 0, memory: 0, entry: 'web', paused: false,
      reducedMotion, playing: false, visible: true, heroVisible: true, storyVisible: false };
  }

  /** 校验离散选择，保证键盘、计时器与按钮遵循同一范围。 */
  select(key, value) {
    const sizes = { phase: phases.length, scene: scenes.length, memory: memories.length };
    if (key === 'entry' ? !Object.hasOwn(entries, value) : !Number.isInteger(value) || value < 0 || value >= sizes[key]) return;
    if (!(key in sizes) && key !== 'entry') return;
    this.update({ [key]: value });
  }

  /** 仅在状态实际改变时通知视图。 */
  update(patch) {
    if (Object.entries(patch).every(([key, value]) => this.state[key] === value)) return;
    this.state = { ...this.state, ...patch };
    this.dispatchEvent(new Event('change'));
  }

  /** 页面隐藏、用户暂停或系统减少动态效果时，不自动推进。 */
  advance(kind) {
    const s = this.state;
    if (s.paused || s.reducedMotion || !s.visible) return;
    if (kind === 'scene' && s.playing && s.storyVisible) this.select('scene', (s.scene + 1) % scenes.length);
  }
}
