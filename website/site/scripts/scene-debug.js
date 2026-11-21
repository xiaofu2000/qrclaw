import { samplePresentation } from './scene-state.js?v=sync-20261020-3';

/** 仅显式诊断入口加载；正常访客不会记录输入或显示面板。 */
export function mountSceneDebug(scene, model) {
  const panel = document.createElement('details');
  panel.id = 'scene-debug';
  panel.style.cssText = 'position:fixed;z-index:100;right:12px;top:88px;background:#101b2e;color:white;padding:12px;font:12px monospace;max-width:340px;max-height:65vh;overflow:auto;border:1px solid #6683aa';
  panel.innerHTML = '<summary>场景诊断</summary><pre aria-label="场景实时指标"></pre><button type="button" data-debug="snapshot">下载当前静帧</button> · <button type="button" data-debug="record">录制前进与回退</button> · <button type="button" data-debug="stress">检查快速反向滑动</button><output aria-label="快速滑动检查结果"></output> · <button type="button" data-debug="reduced">预览减少动态</button> · <button type="button" data-debug="context">模拟显卡中断</button>';
  document.body.append(panel);
  const scrollLog = document.createElement('pre');
  scrollLog.setAttribute('aria-label', '滚动诊断日志');
  panel.append(scrollLog);
  const counters = { 输入次数:0, 被阻止次数:0, 滚动次数:0 };
  const events = [];
  let lastReport = 0;
  /** 日志只保留最近 30 项；统计全部输入，避免大量 console 输出影响滚动。 */
  function record(type, detail = {}) {
    const item = { 时间:Math.round(performance.now()), 类型:type, 位置:Math.round(scrollY), ...detail };
    events.push(item);
    if (events.length > 30) events.shift();
    if (performance.now() - lastReport > 250) {
      console.debug('滚动诊断', item);
      lastReport = performance.now();
    }
  }
  /** 事件传播结束后只读取取消状态，诊断监听器自身不阻止默认行为。 */
  function observeInput(event) {
    queueMicrotask(() => {
      counters.输入次数 += 1;
      if (event.defaultPrevented) counters.被阻止次数 += 1;
      record(event.type === 'wheel' ? '滚轮输入' : '触摸输入', {
        被阻止:event.defaultPrevented, 输入量:Math.round(event.deltaY || 0),
      });
    });
  }
  function observeScroll() {
    counters.滚动次数 += 1;
    record('页面滚动');
  }
  window.addEventListener('wheel', observeInput, { passive:true });
  window.addEventListener('touchmove', observeInput, { passive:true });
  window.addEventListener('scroll', observeScroll, { passive:true });
  const timer = setInterval(() => {
    panel.querySelector('pre').textContent = JSON.stringify(scene.metrics(), null, 2);
    scrollLog.textContent = JSON.stringify({ ...counters, 当前滚动位置:Math.round(scrollY), 最近事件:events }, null, 2);
  }, 300);
  panel.querySelector('[data-debug="snapshot"]').addEventListener('click', async () => {
    const blob = await scene.snapshot();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = innerWidth <= 900 ? 'poster-mobile.png' : 'poster-desktop.png';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  panel.querySelector('[data-debug="reduced"]').addEventListener('click', () => {
    model.update({ reducedMotion: !model.state.reducedMotion });
    window.dispatchEvent(new Event('resize'));
  });
  panel.querySelector('[data-debug="context"]').addEventListener('click', () => scene.loseContext());
  /** 连续跳章并逐帧比较真实镜头与 DOM，防止只检查静止截图漏掉时序错误。 */
  panel.querySelector('[data-debug="stress"]').addEventListener('click', event => {
    const button = event.currentTarget;
    button.disabled = true;
    model.update({ paused:false, reducedMotion:false });
    const hero = document.querySelector('#top');
    const steps = [0, 2 / 3, 1 / 3, 1, 0, 2 / 3, 0];
    const result = { frames:0, mismatches:0, movingFrames:0 };
    function inspect() {
      if (result.frames % 12 === 0) {
        const progress = steps[Math.floor(result.frames / 12) % steps.length];
        window.scrollTo({ top:hero.offsetTop + (hero.offsetHeight - innerHeight) * progress, behavior:'instant' });
      }
      const progress = scene.metrics().rendered;
      const expected = samplePresentation(progress);
      const stage = document.querySelector('.scene-stage');
      const visible = [...document.querySelectorAll('.scene-stage > [data-chapter]')].filter(el => !el.hidden);
      if (Math.abs(Number(stage.dataset.presentation) - progress) > 1e-8
        || Number(stage.dataset.chapter) !== expected.chapter
        || visible.length !== (expected.opacity > 0 ? 1 : 0)
        || visible.some(el => Number(el.dataset.chapter) !== expected.chapter)) result.mismatches += 1;
      if (expected.opacity < 1) result.movingFrames += 1;
      result.frames += 1;
      if (result.frames < 240) requestAnimationFrame(inspect);
      else {
        panel.querySelector('output').textContent = JSON.stringify(result);
        button.disabled = false;
        window.scrollTo({ top:0, behavior:'instant' });
      }
    }
    requestAnimationFrame(inspect);
  });
  /** 只推动原生滚动，录下同一渲染器的真实前进/回退，不另写演示相机。 */
  panel.querySelector('[data-debug="record"]').addEventListener('click', event => {
    const button = event.currentTarget;
    const canvas = document.querySelector('#brain-canvas');
    if (!canvas.captureStream || !window.MediaRecorder) {
      button.textContent = '当前浏览器不支持录制';
      button.disabled = true;
      return;
    }
    button.disabled = true;
    const stream = canvas.captureStream(30);
    const recorder = new MediaRecorder(stream);
    const chunks = [];
    recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: recorder.mimeType });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = recorder.mimeType.includes('mp4') ? 'brain-camera-tour.mp4' : 'brain-camera-tour.webm';
      link.click();
      stream.getTracks().forEach(track => track.stop());
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      button.disabled = false;
    };
    model.update({ paused:false, reducedMotion:false });
    const hero = document.querySelector('#top');
    const distance = hero.offsetHeight - innerHeight;
    const start = performance.now();
    recorder.start();
    function advance(now) {
      const elapsed = (now - start) / 1000;
      const progress = elapsed <= 4.5 ? elapsed / 4.5 : Math.max(0, 1 - (elapsed - 4.5) / 4.5);
      window.scrollTo({ top:hero.offsetTop + distance * progress, behavior:'instant' });
      if (elapsed < 10) requestAnimationFrame(advance);
      else recorder.stop();
    }
    requestAnimationFrame(advance);
  });
  window.addEventListener('pagehide', () => {
    clearInterval(timer);
    window.removeEventListener('wheel', observeInput);
    window.removeEventListener('touchmove', observeInput);
    window.removeEventListener('scroll', observeScroll);
    panel.remove();
  }, { once:true });
}
