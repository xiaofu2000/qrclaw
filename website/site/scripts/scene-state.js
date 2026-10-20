/** 将滚动值分配到相邻镜头；端点保留停顿，给文字留下阅读时间。
 * 停顿区与三次缓动方法改编自 Keyframe Scroll Odyssey，MIT，见 credits.html。
 */
export function sampleTimeline(progress) {
  const value = Number.isFinite(progress) ? Math.max(0, Math.min(1, progress)) : 0;
  const count = 4;
  const position = value * (count - 1);
  const index = Math.min(Math.floor(position), count - 2);
  const local = Math.max(0, Math.min(1, (position - index - 0.12) / 0.76));
  const blend = local < 0.5 ? 4 * local ** 3 : 1 - (-2 * local + 2) ** 3 / 2;
  return { index, blend, phase: Math.min(count - 1, Math.floor(position + 0.5)) };
}
