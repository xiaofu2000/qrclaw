/** 仅本地诊断入口使用，正式访客不会加载此面板。 */
export function mountSceneDebug(scene, model) {
  const panel = document.createElement('details');
  panel.id = 'scene-debug';
  panel.style.cssText = 'position:fixed;z-index:100;right:12px;top:88px;background:#101b2e;color:white;padding:12px;font:12px monospace;max-width:340px;max-height:65vh;overflow:auto;border:1px solid #6683aa';
  panel.innerHTML = '<summary>场景诊断</summary><pre aria-label="场景实时指标"></pre><button type="button" data-debug="snapshot">下载当前静帧</button> · <button type="button" data-debug="record">录制前进与回退</button> · <button type="button" data-debug="reduced">预览减少动态</button> · <button type="button" data-debug="context">模拟显卡中断</button>';
  document.body.append(panel);
  const timer = setInterval(() => { panel.querySelector('pre').textContent = JSON.stringify(scene.metrics(), null, 2); }, 300);
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
  window.addEventListener('pagehide', () => { clearInterval(timer); panel.remove(); }, { once:true });
}
