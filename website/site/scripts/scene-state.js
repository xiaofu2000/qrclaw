/** 将滚动值分配到相邻镜头；端点保留停顿，给文字留下阅读时间。
 * 停顿区与三次缓动方法改编自 Keyframe Scroll Odyssey，MIT，见 credits.html。
 */
export function sampleTimeline(progress) {
  const value = Number.isFinite(progress) ? Math.max(0, Math.min(1, progress)) : 0;
  const count = 4;
  const position = value * (count - 1);
  const index = Math.min(Math.floor(position), count - 2);
  const local = Math.max(0, Math.min(1, (position - index - 0.12) / 0.76));
  const blend = local * local * (3 - 2 * local);
  return { index, blend, phase: Math.min(count - 1, Math.floor(position + 0.5)) };
}

/** 文案只在镜头端点附近出现；横移途中留给主体，快滑和倒退沿用同一时间轴。 */
export function samplePresentation(progress) {
  const { index, blend } = sampleTimeline(progress);
  const position = index + blend;
  const chapter = Math.round(position);
  const fade = Math.max(0, Math.min(1, (Math.abs(position - chapter) - 0.04) / 0.14));
  const opacity = 1 - fade * fade * (3 - 2 * fade);
  const reveal = Math.max(0, Math.min(1, (position - 2.7) / 0.3));
  return { chapter, opacity, offset: 24 * (1 - opacity), bridge: reveal * reveal * (3 - 2 * reveal) };
}
