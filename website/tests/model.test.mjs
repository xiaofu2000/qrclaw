import test from 'node:test';
import assert from 'node:assert/strict';
import { WebsiteModel } from '../site/scripts/model.js';

test('无效选择不会越界或改变状态', () => {
  const model = new WebsiteModel();
  for (const value of [-1, 3, NaN, '1', Infinity]) model.select('scene', value);
  model.select('entry', '__proto__');
  model.select('missing', 0);
  assert.equal(model.state.scene, 0);
  assert.equal(model.state.entry, 'web');
  assert.equal(model.state.missing, undefined);
});

test('暂停、系统减少动画和后台页面均阻止自动推进', () => {
  for (const patch of [{ paused: true }, { reducedMotion: true }, { visible: false }]) {
    const model = new WebsiteModel();
    model.update({ playing: true, storyVisible: true, ...patch });
    model.advance('phase');
    model.advance('scene');
    assert.equal(model.state.phase, 0);
    assert.equal(model.state.scene, 0);
    model.select('scene', 1);
    assert.equal(model.state.scene, 1, '减少动画仍允许主动查看每个步骤');
  }
});

test('离开对应区域暂停推进，回放循环保持有效索引', () => {
  const model = new WebsiteModel();
  model.update({ playing: true, heroVisible: false, storyVisible: false });
  model.advance('phase');
  model.advance('scene');
  assert.equal(model.state.phase, 0);
  assert.equal(model.state.scene, 0);
  model.update({ heroVisible: true, storyVisible: true });
  for (let i = 0; i < 4; i++) { model.advance('scene'); model.advance('phase'); }
  assert.equal(model.state.scene, 1);
  assert.equal(model.state.phase, 0, '滚动阶段不再由计时器推进');
});

test('没有播放授权时，任务演示保持在所选步骤', () => {
  const model = new WebsiteModel();
  model.update({ storyVisible: true });
  model.advance('scene');
  assert.equal(model.state.scene, 0);
  let changes = 0;
  model.addEventListener('change', () => changes++);
  model.select('memory', 2);
  model.select('memory', 2);
  assert.equal(changes, 1);
});
