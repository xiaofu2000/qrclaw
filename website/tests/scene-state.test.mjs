import test from 'node:test';
import assert from 'node:assert/strict';
import { sampleTimeline } from '../site/scripts/scene-state.js';

test('镜头进度限制在有效端点，异常输入回到首屏', () => {
  for (const value of [NaN, Infinity, -1, undefined]) assert.deepEqual(sampleTimeline(value), { index: 0, blend: 0, phase: 0 });
  assert.deepEqual(sampleTimeline(2), { index: 2, blend: 1, phase: 3 });
});

test('向下滚动只向前推进，四个关键镜头稳定停留', () => {
  let last = 0;
  for (let i = 0; i <= 1000; i++) {
    const { index, blend, phase } = sampleTimeline(i / 1000);
    assert(index + blend >= last);
    assert(phase >= 0 && phase <= 3);
    last = index + blend;
  }
  for (const [progress, position] of [[0.01, 0], [1 / 3, 1], [2 / 3, 2], [0.99, 3]]) {
    const frame = sampleTimeline(progress);
    assert.equal(frame.index + frame.blend, position);
  }
});
