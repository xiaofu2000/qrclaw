import { CatmullRomCurve3, Vector3, MathUtils } from 'three';
import { sampleTimeline } from '../site/scripts/scene-state.js';

/** 正式官网四个镜头端点；窄屏为上方文案保留独立安全区。 */
export const cameraChapters = {
  desktop: [
    { position: [-2.5, 0.4, 9.5], target: [-1.35, -0.15, 0], fov: 34 },
    { position: [-0.4, 0.9, 7.2], target: [-1.6, 0, 0], fov: 35 },
    { position: [3.8, 1.4, 6.2], target: [1.7, 0.1, 0], fov: 36 },
    { position: [4.2, 3.2, 10.8], target: [2.4, 1.75, 0], fov: 35 },
  ],
  compact: [
    { position: [-2.5, 0.35, 12], target: [0, 0.65, 0], fov: 34 },
    { position: [0.4, 1.2, 9.8], target: [-0.25, 0.55, 0], fov: 36 },
    { position: [3.8, 1.6, 8.6], target: [0.3, 0.5, 0], fov: 37 },
    { position: [4.5, 3.4, 13], target: [0, 1.5, 0], fov: 39 },
  ],
};

/** 位置和视线分别沿曲线移动，避免直接插值欧拉角造成翻转。 */
export function createCameraPath(compact) {
  const chapters = compact ? cameraChapters.compact : cameraChapters.desktop;
  const positions = new CatmullRomCurve3(chapters.map(item => new Vector3(...item.position)), false, 'centripetal');
  const targets = new CatmullRomCurve3(chapters.map(item => new Vector3(...item.target)), false, 'centripetal');
  return progress => {
    const { index, blend, phase } = sampleTimeline(progress);
    const point = (index + blend) / (chapters.length - 1);
    return {
      position: positions.getPoint(point),
      target: targets.getPoint(point),
      fov: MathUtils.lerp(chapters[index].fov, chapters[index + 1].fov, blend),
      phase,
    };
  };
}

/** 采用已安装 Scroll Worlds skill 的帧率无关阻尼；长暂停后不跨越整条路径。 */
export function dampProgress(current, target, seconds) {
  const dt = Math.max(0, Math.min(Number.isFinite(seconds) ? seconds : 0, 1 / 30));
  const next = current + (target - current) * (1 - Math.exp(-3.2 * dt));
  return Math.abs(next - target) < 0.00005 ? target : next;
}
