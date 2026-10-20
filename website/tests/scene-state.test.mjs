import test from 'node:test';
import assert from 'node:assert/strict';
import { sampleTimeline, samplePresentation } from '../site/scripts/scene-state.js';
import { dampProgress } from '../src/brain-camera.js';

test('镜头进度限制在有效端点，异常输入回到首屏', () => {
  for (const value of [NaN, Infinity, -1, undefined]) assert.deepEqual(sampleTimeline(value), { index: 0, blend: 0, phase: 0 });
  assert.deepEqual(sampleTimeline(2), { index: 2, blend: 1, phase: 3 });
});

test('文字在镜头端点可读，主体横移时完全退场', () => {
  for (let chapter = 0; chapter < 4; chapter += 1) {
    const frame = samplePresentation(chapter / 3);
    assert.equal(frame.chapter, chapter);
    assert.equal(frame.opacity, 1);
  }
  for (const progress of [1 / 6, 0.5, 5 / 6]) assert.equal(samplePresentation(progress).opacity, 0);
  assert.equal(samplePresentation(0).bridge, 0);
  assert.equal(samplePresentation(1).bridge, 1);
});

test('快速跨章节与反向滑动期间，文字只属于实际镜头附近的章节', () => {
  let rendered = 0;
  for (const target of [2 / 3, 0, 1, 1 / 3, 0]) {
    for (let frame = 0; frame < 90; frame += 1) {
      rendered = dampProgress(rendered, target, 1 / 60);
      const text = samplePresentation(rendered);
      const camera = sampleTimeline(rendered);
      if (text.opacity > 0) assert.ok(Math.abs(camera.index + camera.blend - text.chapter) < 0.18);
      assert.ok(text.opacity >= 0 && text.opacity <= 1);
    }
  }
});

test('向下滚动只向前推进，四个关键镜头稳定停留', () => {
  let last = 0;
  for (let i = 0; i <= 1000; i++) {
    const { index, blend, phase } = sampleTimeline(i / 1000);
    assert(index + blend >= last);
    assert.ok(index + blend - last < 0.0065, '章节中段不能突然加速横扫画面');
    assert(phase >= 0 && phase <= 3);
    last = index + blend;
  }
  for (const [progress, position] of [[0.01, 0], [1 / 3, 1], [2 / 3, 2], [0.99, 3]]) {
    const frame = sampleTimeline(progress);
    assert.equal(frame.index + frame.blend, position);
  }
});
