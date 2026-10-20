import { readFile, writeFile } from 'node:fs/promises';

/** 生成临时测量页，原页面结构和资源不变；发布前必须删除。 */
const html = await readFile(new URL('../site/index.html', import.meta.url), 'utf8');
const instrumentation = `<script>
(() => {
  const metrics = { lcp: null, cls: 0, interaction: null, frameP50: null, frameP95: null };
  function report() {
    document.documentElement.dataset.verificationMetrics = JSON.stringify(metrics);
  }
  for (const type of ['largest-contentful-paint', 'layout-shift', 'event']) {
    try {
      new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          if (type === 'largest-contentful-paint') metrics.lcp = Math.round(entry.startTime);
          if (type === 'layout-shift' && !entry.hadRecentInput) metrics.cls += entry.value;
          if (type === 'event' && entry.interactionId) metrics.interaction = Math.max(metrics.interaction || 0, entry.duration);
        }
        report();
      }).observe({type, buffered:true, durationThreshold:16});
    } catch {}
  }
  const frames = [];
  let previous;
  function sample(now) {
    if (previous) frames.push(now - previous);
    previous = now;
    if (frames.length < 120) requestAnimationFrame(sample);
    else {
      frames.sort((a,b) => a-b);
      metrics.frameP50 = Math.round(frames[60] * 10) / 10;
      metrics.frameP95 = Math.round(frames[114] * 10) / 10;
      report();
    }
  }
  addEventListener('load', () => setTimeout(() => requestAnimationFrame(sample), 1500));
})();
</script>`;
await writeFile(new URL('../site/__check.html', import.meta.url), html.replace('<head>', '<head>' + instrumentation));
console.log('临时浏览器测量页已生成；完成后删除 site/__check.html。');
