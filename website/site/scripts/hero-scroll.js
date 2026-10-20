/** 合并一次滚轮/触控板手势及其惯性尾部，避免同一手势连续切章。 */
export function createWheelGesture() {
  let lastTime = -Infinity;
  let distance = 0;
  let used = false;
  return (delta, time, settled) => {
    if (!Number.isFinite(delta) || delta === 0) return 0;
    if (time - lastTime > 260) { distance = 0; used = false; }
    lastTime = time;
    if (used) return 0;
    // 镜头未到位时开始的新手势整体忽略，不能让它的尾部变成下一次切换。
    if (!settled) { used = true; return 0; }
    if (Math.sign(delta) !== Math.sign(distance)) distance = 0;
    distance += delta;
    if (Math.abs(distance) < 40) return 0;
    used = true;
    return Math.sign(distance);
  };
}

/** 首屏一次手势推进一段；首尾向外滑动、缩放和正文滚动仍交还浏览器。 */
export function bindHeroScroll(hero, { getPhase, isSettled, goTo }) {
  const wheelGesture = createWheelGesture();
  let touch = null;
  const inside = () => scrollY >= hero.offsetTop - 1 && scrollY <= hero.offsetTop + hero.offsetHeight - innerHeight + 1;
  const excluded = target => target?.closest?.('nav.open, #scene-debug, input, textarea, select, [contenteditable="true"]');
  const outside = phase => phase < 0 || phase > 3;

  function onWheel(event) {
    if (!inside() || excluded(event.target) || !event.cancelable || event.ctrlKey || event.metaKey
      || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? innerHeight : 1;
    const direction = wheelGesture(event.deltaY * unit, performance.now(), isSettled());
    if (direction && outside(getPhase() + direction)) return;
    event.preventDefault();
    if (direction) goTo(getPhase() + direction);
  }

  function onTouchStart(event) {
    touch = inside() && !excluded(event.target) && event.touches.length === 1
      ? { x:event.touches[0].clientX, y:event.touches[0].clientY, phase:getPhase(), ready:isSettled(), used:false }
      : null;
  }

  function onTouchMove(event) {
    if (!touch || event.touches.length !== 1 || !event.cancelable) { touch = null; return; }
    const dx = event.touches[0].clientX - touch.x;
    const dy = touch.y - event.touches[0].clientY;
    if (Math.abs(dy) <= Math.abs(dx)) return;
    const next = touch.phase + Math.sign(dy);
    if (touch.ready && !touch.used && outside(next)) return;
    event.preventDefault();
    if (touch.ready && !touch.used && Math.abs(dy) >= 40) {
      touch.used = true;
      goTo(next);
    }
  }

  function onTouchEnd() { touch = null; }
  window.addEventListener('wheel', onWheel, { passive:false });
  window.addEventListener('touchstart', onTouchStart, { passive:true });
  window.addEventListener('touchmove', onTouchMove, { passive:false });
  window.addEventListener('touchend', onTouchEnd);
  window.addEventListener('touchcancel', onTouchEnd);
  return () => {
    window.removeEventListener('wheel', onWheel);
    window.removeEventListener('touchstart', onTouchStart);
    window.removeEventListener('touchmove', onTouchMove);
    window.removeEventListener('touchend', onTouchEnd);
    window.removeEventListener('touchcancel', onTouchEnd);
  };
}
