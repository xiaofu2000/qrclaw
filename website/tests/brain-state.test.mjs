import test from 'node:test';
import assert from 'node:assert/strict';
import { brainProgress } from '../site/scripts/brain-state.js';

test('镜头在首尾停留，异常进度回到安全位置', () => {
  assert.equal(brainProgress(-1), 0);
  assert.equal(brainProgress(0.1), 0);
  assert.equal(brainProgress(0.5), 0.5);
  assert.equal(brainProgress(0.9), 1);
  assert.equal(brainProgress(Infinity), 0);
  assert.equal(brainProgress(NaN), 0);
  let previous = 0;
  for (let i = 0; i <= 100; i += 1) {
    const value = brainProgress(i / 100);
    assert.ok(value >= previous && value <= 1);
    previous = value;
  }
});

test('减少动态效果时只显示两个完整镜头', () => {
  assert.equal(brainProgress(0.49, true), 0);
  assert.equal(brainProgress(0.5, true), 1);
  assert.equal(brainProgress(NaN, true), 0);
});
