/** 镜头两端留出阅读停顿；无效滚动值回到首屏。 */
export function brainProgress(progress, staticMode = false) {
  const clamped = Number.isFinite(progress) ? Math.max(0, Math.min(1, progress)) : 0;
  if (staticMode) return clamped < 0.5 ? 0 : 1;
  const moving = Math.max(0, Math.min(1, (clamped - 0.18) / 0.64));
  return moving * moving * (3 - 2 * moving);
}
