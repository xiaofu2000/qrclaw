import test from 'node:test';
import assert from 'node:assert/strict';
import { createCameraPath, dampProgress } from '../src/brain-camera.js';

test('前进、回退与深处刷新在同一位置得到相同镜头，整条曲线不穿过模型', () => {
  for (const compact of [false, true]) {
    const path = createCameraPath(compact);
    const poses = Array.from({ length: 601 }, (_, index) => path(index / 600));
    for (let index = poses.length - 1; index >= 0; index -= 1) {
      const pose = path(index / 600);
      assert.deepEqual(pose, poses[index]);
      assert.ok(pose.position.length() > 5, '相机必须留在大脑几何体之外');
      assert.ok(pose.position.distanceTo(pose.target) > 4, '视线不能在路径中翻转');
      if (index > 0) assert.ok(pose.position.distanceTo(poses[index - 1].position) < 0.2, '路径不能跳点');
    }
  }
});

test('不同刷新率的阻尼一致，快速反向也不会越过目标', () => {
  const advance = (fps, target) => {
    let value = 0;
    for (let index = 0; index < fps; index += 1) value = dampProgress(value, target, 1 / fps);
    return value;
  };
  assert.ok(Math.abs(advance(60, 1) - advance(120, 1)) < 1e-10);
  let value = 0.8;
  for (let index = 0; index < 120; index += 1) {
    const next = dampProgress(value, 0.2, 1 / 60);
    assert.ok(next <= value && next >= 0.2);
    value = next;
  }
  assert.ok(dampProgress(0, 1, 20) < 0.2, '后台恢复后的大时间步不得跳跃');
});
