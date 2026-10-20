import { WebsiteModel } from './model.js';
import { phases, scenes, memories, entries } from './content.js';
import { sampleTimeline } from './scene-state.js';

const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
const model = new WebsiteModel(reduced.matches);
const phaseButtons = [...document.querySelectorAll('[data-phase]')];
const sceneButtons = [...document.querySelectorAll('[data-scene]')];
const memoryButtons = [...document.querySelectorAll('[data-memory]')];
const entryButtons = [...document.querySelectorAll('[data-entry]')];
const motionButton = document.querySelector('#motion-toggle');
const playbackButton = document.querySelector('#story-play');
let brain;
let rendered = {};
let toastTimer;

/** 用可读文字提供复制成功与失败反馈。 */
function showToast(text) {
  const toast = document.querySelector('#toast');
  toast.textContent = text;
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}

/** 同步选中状态，避免鼠标和键盘显示不一致。 */
function renderSelection(buttons, selected, attribute, key) {
  buttons.forEach(button => {
    const active = button.dataset[key] === String(selected);
    button.setAttribute(attribute, String(active));
    button.classList.toggle('active', active);
    if (attribute === 'aria-selected') button.tabIndex = active ? 0 : -1;
  });
}

/** 渲染静态内容与离散状态；每帧 WebGL 更新不触发 DOM 渲染。 */
function render() {
  const state = model.state;
  if (state.phase !== rendered.phase) {
    renderSelection(phaseButtons, state.phase, 'aria-pressed', 'phase');
    document.querySelector('#phase-description').textContent = phases[state.phase];
    phaseButtons.forEach((button, index) => {
      if (index === state.phase) button.setAttribute('aria-describedby', 'phase-description');
      else button.removeAttribute('aria-describedby');
    });
  }
  if (state.scene !== rendered.scene) {
    const scene = scenes[state.scene];
    renderSelection(sceneButtons, state.scene, 'aria-selected', 'scene');
    document.querySelector('#product-panel').setAttribute('aria-labelledby', `story-tab-${state.scene}`);
    document.querySelector('#scene-name').textContent = scene.title;
    document.querySelector('#scene-caption').textContent = scene.caption;
    document.querySelector('#scene-index').textContent = `0${state.scene + 1} / 03`;
    const image = document.querySelector('#product-image');
    image.src = scene.image;
    image.alt = scene.alt;
    document.querySelector('#product-full').href = scene.image;
    image.classList.remove('changing');
    if (!state.reducedMotion && !state.paused) requestAnimationFrame(() => image.classList.add('changing'));
  }
  if (state.memory !== rendered.memory) {
    const item = memories[state.memory];
    renderSelection(memoryButtons, state.memory, 'aria-pressed', 'memory');
    for (const [id, value] of Object.entries({ 'memory-slug': item.slug, 'memory-note-title': item.title,
      'memory-note-copy': item.copy, 'memory-source': item.source })) document.getElementById(id).textContent = value;
    document.querySelector('#memory-tags').replaceChildren(...item.tags.map(tag => {
      const span = document.createElement('span');
      span.textContent = tag;
      return span;
    }));
  }
  if (state.entry !== rendered.entry) {
    const entry = entries[state.entry];
    renderSelection(entryButtons, state.entry, 'aria-selected', 'entry');
    document.querySelector('#entry-panel').setAttribute('aria-labelledby', `entry-tab-${state.entry}`);
    for (const [id, value] of Object.entries({ 'entry-heading': entry.title, 'entry-copy': entry.copy,
      'entry-file': entry.file, 'entry-code': entry.code })) document.getElementById(id).textContent = value;
  }
  document.documentElement.classList.toggle('motion-paused', state.paused || state.reducedMotion);
  document.documentElement.classList.toggle('page-hidden', !state.visible);
  motionButton.setAttribute('aria-pressed', String(state.paused));
  motionButton.setAttribute('aria-label', state.reducedMotion ? '已减少动态效果' : state.paused ? '继续页面动画' : '暂停页面动画');
  motionButton.querySelector('span').textContent = state.reducedMotion ? '已减少动态效果' : state.paused ? '继续动画' : '暂停动画';
  motionButton.disabled = state.reducedMotion;
  playbackButton.setAttribute('aria-pressed', String(state.playing));
  playbackButton.querySelector('span:last-child').textContent = state.playing ? '暂停步骤播放' : '自动播放步骤';
  playbackButton.disabled = state.reducedMotion || state.paused;
  brain?.update(state.progress, state.paused || state.reducedMotion);
  rendered = { ...state };
}

/** 为选项组提供方向键与首尾跳转，随后保持焦点可见。 */
function wireTabs(buttons, select) {
  buttons.forEach((button, index) => button.addEventListener('keydown', event => {
    const keys = ['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End'];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    let next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (index + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1) + buttons.length) % buttons.length;
    select(buttons[next]);
    buttons[next].focus();
  }));
}

/** 阶段按钮使用相同滚动坐标，不建立另一套动画状态。 */
phaseButtons.forEach(button => button.addEventListener('click', () => {
  const hero = document.querySelector('#top');
  const progress = Number(button.dataset.phase) / 3;
  window.scrollTo({ top: hero.offsetTop + progress * (hero.offsetHeight - innerHeight), behavior: 'instant' });
  updateScroll(true);
}));
sceneButtons.forEach(button => button.addEventListener('click', () => { model.update({ playing: false }); model.select('scene', Number(button.dataset.scene)); }));
memoryButtons.forEach(button => button.addEventListener('click', () => model.select('memory', Number(button.dataset.memory))));
entryButtons.forEach(button => button.addEventListener('click', () => model.select('entry', button.dataset.entry)));
wireTabs(sceneButtons, button => { model.update({ playing: false }); model.select('scene', Number(button.dataset.scene)); });
wireTabs(entryButtons, button => model.select('entry', button.dataset.entry));
motionButton.addEventListener('click', () => model.update({ paused: !model.state.paused }));
playbackButton.addEventListener('click', () => model.update({ playing: !model.state.playing }));
document.addEventListener('visibilitychange', () => model.update({ visible: !document.hidden }));
reduced.addEventListener('change', event => model.update({ reducedMotion: event.matches, playing: false }));
model.addEventListener('change', render);
render();

document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
  const code = document.getElementById(button.dataset.copy);
  try {
    await navigator.clipboard.writeText(code.textContent);
    showToast('命令已复制');
  } catch {
    const range = document.createRange();
    range.selectNodeContents(code);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    showToast('浏览器未允许复制，已选中文字，请手动复制。');
  }
}));

const menuButton = document.querySelector('.menu-button');
const nav = document.querySelector('#main-nav');
/** 收起手机导航；按 Escape 时恢复到菜单按钮。 */
function closeMenu(restoreFocus = false) {
  menuButton.setAttribute('aria-expanded', 'false');
  menuButton.setAttribute('aria-label', '打开导航');
  nav.classList.remove('open');
  if (restoreFocus) menuButton.focus();
}
menuButton.addEventListener('click', () => {
  const open = menuButton.getAttribute('aria-expanded') !== 'true';
  menuButton.setAttribute('aria-expanded', String(open));
  menuButton.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
  nav.classList.toggle('open', open);
});
nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => closeMenu()));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && nav.classList.contains('open')) closeMenu(true);
});
document.addEventListener('click', event => {
  if (!event.target.closest('.site-header')) closeMenu();
});
window.matchMedia('(min-width: 721px)').addEventListener('change', () => closeMenu());

const observer = new IntersectionObserver(entries => {
  for (const entry of entries) {
    entry.target.classList.toggle('motion-in-view', entry.isIntersecting);
    if (entry.target.id === 'top') model.update({ heroVisible: entry.isIntersecting });
    if (entry.target.id === 'workflow') model.update({ storyVisible: entry.isIntersecting });
    if (entry.isIntersecting) entry.target.classList.add('in-view');
  }
}, { threshold: 0.06 });
document.querySelectorAll('.section').forEach(section => {
  section.classList.add('reveal-ready');
  observer.observe(section);
});
observer.observe(document.querySelector('#top'));
const hero = document.querySelector('#top');
const chapterElements = [...document.querySelectorAll('[data-chapter]')];
let scrollFrame = 0;
/** 滚动位置属于视图模型；暂停时直接切换关键状态，保持阅读位置正确。 */
function updateScroll(force = false) {
  scrollFrame = 0;
  const progress = Math.max(0, Math.min(1, -hero.getBoundingClientRect().top / Math.max(1, hero.offsetHeight - innerHeight)));
  const { phase } = sampleTimeline(progress);
  const presentation = model.state.reducedMotion || model.state.paused ? phase / 3 : progress;
  model.update({ progress: presentation, phase });
  chapterElements.forEach(el => { el.hidden = Number(el.dataset.chapter) !== phase; });
  document.querySelector('.scene-stage').dataset.chapter = String(phase);
  chapterElements.forEach(el => {
    const distance = Math.abs(presentation * 3 - Number(el.dataset.chapter));
    el.style.opacity = String(Math.max(0, Math.min(1, (0.5 - distance) / 0.15)));
  });
  if (!brain) renderBrainTransition(presentation);
}

/** 工作台交接跟随实际镜头进度；导航与可访问状态仍使用精确滚动位置。 */
function renderBrainTransition(presentation) {
  const bridge = document.querySelector('.scene-bridge');
  const reveal = Math.max(0, Math.min(1, (presentation - 0.78) / 0.18));
  bridge.style.opacity = String(reveal);
  bridge.style.transform = `translateY(${(1 - reveal) * 48}px) scale(${0.94 + reveal * 0.06})`;
  document.querySelector('.hero-art').style.opacity = String(1 - reveal * 0.72);
}
window.addEventListener('scroll', () => { if (!scrollFrame) scrollFrame = requestAnimationFrame(() => updateScroll()); }, { passive: true });
window.addEventListener('resize', () => updateScroll(true));
motionButton.addEventListener('click', () => updateScroll(true));
reduced.addEventListener('change', () => updateScroll(true));
updateScroll(true);
const sceneTimer = setInterval(() => model.advance('scene'), 5000);

/** 先展示真实模型静帧；三维失败时保持全部正文与导航可用。 */
async function loadBrain() {
  const art = document.querySelector('.hero-art');
  const status = document.querySelector('#brain-status');
  try {
    const { createBrainScene } = await import('./brain-scene.js');
    brain = await createBrainScene(document.querySelector('#brain-canvas'), {
      onFrame: renderBrainTransition,
      onFailure(message) {
        art.classList.toggle('ready', !message);
        status.hidden = !message;
        status.textContent = message || '';
      },
    });
    brain.update(model.state.progress, model.state.paused || model.state.reducedMotion);
    art.classList.add('ready');
    status.hidden = true;
    if (location.hostname === '127.0.0.1' && new URLSearchParams(location.search).has('scene-debug')) {
      const { mountSceneDebug } = await import('./scene-debug.js');
      mountSceneDebug(brain, model);
    }
  } catch (error) {
    status.textContent = '三维画面暂未加载，已显示静态画面，你仍可浏览全部内容。';
    console.warn('大脑场景未能加载，已保留静态画面与全部页面功能。', error.message);
  }
}
if ('requestIdleCallback' in window) requestIdleCallback(loadBrain, { timeout: 1500 });
else setTimeout(loadBrain, 200);

window.addEventListener('pagehide', event => {
  if (event.persisted) model.update({ visible: false });
  else {
    cancelAnimationFrame(scrollFrame);
    clearInterval(sceneTimer);
    observer.disconnect();
    brain?.dispose();
  }
});
window.addEventListener('pageshow', () => model.update({ visible: !document.hidden }));
