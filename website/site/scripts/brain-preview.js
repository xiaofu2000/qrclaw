import { brainProgress } from './brain-state.js';

const track = document.querySelector('.scroll-track');
const stage = document.querySelector('.stage');
const status = document.querySelector('#model-status');
const toggle = document.querySelector('#motion-toggle');
const chapters = [...document.querySelectorAll('.chapter')];
const phaseLinks = [...document.querySelectorAll('[data-phase]')];
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let paused = reduced.matches;
let scene;
let pendingFrame = false;

/** 让文字和镜头共用进度，静态模式仍能访问第二阶段。 */
function update() {
  pendingFrame = false;
  const distance = Math.max(1, track.offsetHeight - stage.offsetHeight);
  const progress = brainProgress(-track.getBoundingClientRect().top / distance, paused);
  const phase = progress < 0.5 ? 0 : 1;
  stage.dataset.phase = String(phase);
  stage.style.setProperty('--intro-opacity', Math.max(0, 1 - progress * 2.5));
  stage.style.setProperty('--intro-y', `${-progress * 45}px`);
  stage.style.setProperty('--detail-opacity', Math.max(0, (progress - 0.5) * 2));
  stage.style.setProperty('--detail-y', `${(1 - progress) * 24}px`);
  chapters.forEach((chapter, index) => { chapter.inert = index !== phase; });
  phaseLinks.forEach((link, index) => {
    if (index === phase) link.setAttribute('aria-current', 'step');
    else link.removeAttribute('aria-current');
  });
  scene?.update(progress / 3, paused);
}

/** 合并同一帧内的滚动事件，避免重复提交渲染。 */
function scheduleUpdate() {
  if (!pendingFrame) {
    pendingFrame = true;
    requestAnimationFrame(update);
  }
}

/** 暂停控制只改变动态表现，不锁住页面滚动。 */
function updateMotion() {
  toggle.setAttribute('aria-pressed', String(paused));
  toggle.textContent = paused ? '启用动态' : '暂停动态';
  update();
}

phaseLinks.forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  const phase = Number(link.dataset.phase);
  const top = window.scrollY + track.getBoundingClientRect().top;
  window.scrollTo({ top: top + phase * (track.offsetHeight - stage.offsetHeight), behavior: paused ? 'instant' : 'smooth' });
}));
toggle.addEventListener('click', () => { paused = !paused; updateMotion(); });
reduced.addEventListener('change', () => { paused = reduced.matches; updateMotion(); });
window.addEventListener('scroll', scheduleUpdate, { passive: true });
window.addEventListener('resize', scheduleUpdate);
window.addEventListener('pagehide', event => { if (!event.persisted) scene?.dispose(); });
updateMotion();

try {
  const { createBrainScene } = await import('./brain-scene.js');
  scene = await createBrainScene(document.querySelector('#brain-canvas'));
  status.hidden = true;
  update();
} catch (error) {
  status.textContent = '大脑模型暂时未能加载，请稍后刷新。';
  console.error('大脑样片加载失败：', error);
}
