import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { createCameraPath, dampProgress } from './brain-camera.js';

/** 加载 AH 原模型；场景只调整材质、灯光和镜头，不生成替代几何体。 */
export async function createBrainScene(canvas, { onFrame, onFailure } = {}) {
  const modelUrl = new URL('../assets/brain/model.glb', import.meta.url);
  const gltf = await new GLTFLoader().loadAsync(modelUrl.href);
  const bounds = new THREE.Box3().setFromObject(gltf.scene);
  const size = bounds.getSize(new THREE.Vector3());
  const diameter = Math.max(size.x, size.y, size.z);
  if (!Number.isFinite(diameter) || diameter <= 0) throw new Error('大脑模型没有有效的几何尺寸。');

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  // 折射必须采样场景背景；透明画布会让 Three.js 的透射缓冲退回白底。
  renderer.setClearColor(0x030916, 1);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, canvas.clientWidth <= 900 ? 1.25 : 1.4));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.18;
  const scene = new THREE.Scene();
  const backdrop = document.createElement('canvas');
  backdrop.width = 256;
  backdrop.height = 256;
  const context = backdrop.getContext('2d');
  const gradient = context.createRadialGradient(130, 172, 0, 130, 172, 175);
  gradient.addColorStop(0, '#1a3765');
  gradient.addColorStop(0.45, '#08162f');
  gradient.addColorStop(1, '#02050d');
  context.fillStyle = gradient;
  context.fillRect(0, 0, 256, 256);
  const backdropTexture = new THREE.CanvasTexture(backdrop);
  backdropTexture.colorSpace = THREE.SRGBColorSpace;
  scene.background = backdropTexture;
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 50);
  // 暗棚中的条形灯只负责反射，不属于大脑模型的几何体。
  const environment = new THREE.Scene();
  environment.background = new THREE.Color(0x020611);
  const panels = [
    { position: [-4, 2, 3], size: [1.2, 7], color: 0xbddaff, intensity: 6.5 },
    { position: [4, 1, 0], size: [0.8, 6], color: 0x639fff, intensity: 3 },
    { position: [0, 5, -2], size: [6, 1.5], color: 0xe1edff, intensity: 6 },
    { position: [0, -3, 2], size: [4, 0.6], color: 0x3462bd, intensity: 1.5 },
  ].map(({ position, size, color, intensity }) => {
    const panel = new THREE.Mesh(
      new THREE.PlaneGeometry(...size),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(intensity), side: THREE.DoubleSide }),
    );
    panel.position.set(...position);
    panel.lookAt(0, 0, 0);
    environment.add(panel);
    return panel;
  });
  const generator = new THREE.PMREMGenerator(renderer);
  const studio = generator.fromScene(environment, 0.04);
  scene.environment = studio.texture;
  scene.environmentIntensity = 1.32;
  panels.forEach(panel => { panel.geometry.dispose(); panel.material.dispose(); });
  generator.dispose();

  const model = new THREE.Group();
  gltf.scene.position.sub(bounds.getCenter(new THREE.Vector3()));
  model.add(gltf.scene);
  model.scale.setScalar(3.2 / diameter);
  model.position.set(0, -0.35, 0);
  model.rotation.set(0.08, -1.05, -0.08);
  scene.add(model);

  const originals = new Set();
  const originalTextures = new Set();
  const glassMaterials = new Set();
  gltf.scene.traverse(node => {
    if (!node.isMesh) return;
    const oldMaterials = Array.isArray(node.material) ? node.material : [node.material];
    oldMaterials.forEach(material => {
      originals.add(material);
      Object.values(material).forEach(value => { if (value?.isTexture) originalTextures.add(value); });
    });
    const glass = new THREE.MeshPhysicalMaterial({
      color: 0xe5f3ff,
      roughness: 0.13,
      metalness: 0,
      transmission: 1,
      thickness: 0.9,
      ior: 1.42,
      attenuationColor: new THREE.Color(0xa8daff),
      attenuationDistance: 4,
      clearcoat: 0,
      transparent: true,
      opacity: 0.68,
      side: THREE.DoubleSide,
      depthWrite: false,
      normalMap: oldMaterials[0].normalMap,
      normalScale: new THREE.Vector2(0.22, 0.22),
    });
    glassMaterials.add(glass);
    node.material = glass;
  });
  const lightRig = new THREE.Group();
  lightRig.position.copy(model.position);
  const rim = new THREE.DirectionalLight(0x7db5ff, 2.3);
  rim.position.set(-4, 2, -3);
  const key = new THREE.DirectionalLight(0xdcecff, 2.4);
  key.position.set(3, 5, 4);
  const fill = new THREE.SpotLight(0xeaf4ff, 22, 0, Math.PI / 5, 0.7, 2);
  fill.position.set(-3, 3, 4);
  for (const light of [rim, key, fill]) light.target = model;
  lightRig.add(rim, key, fill);
  scene.add(lightRig);

  let progress = 0;
  let smoothProgress = 0;
  let initialized = false;
  let lastTime = 0;
  let paused = false;
  let inView = true;
  let frame = 0;
  let disposed = false;
  let contextLost = false;
  let compact = canvas.clientWidth <= 900;
  let cameraPath = createCameraPath(compact);
  const frameIntervals = [];

  /** 原生滚动只给出目标，镜头沿独立位置/视线曲线平滑到达。 */
  function draw(time = 0) {
    frame = 0;
    if (disposed || contextLost || !inView || document.hidden) { lastTime = 0; return; }
    const elapsed = lastTime ? (time - lastTime) / 1000 : 1 / 60;
    if (lastTime && elapsed < 0.25) {
      frameIntervals.push(elapsed * 1000);
      if (frameIntervals.length > 180) frameIntervals.shift();
    }
    lastTime = time;
    smoothProgress = paused ? progress : dampProgress(smoothProgress, progress, elapsed);
    const pose = cameraPath(smoothProgress);
    camera.position.copy(pose.position);
    camera.lookAt(pose.target);
    // 灯组和棚灯反射沿同一平滑视角转动，回退或暂停时不会独自跳变。
    lightRig.quaternion.copy(camera.quaternion);
    scene.environmentRotation.setFromQuaternion(camera.quaternion);
    if (camera.fov !== pose.fov) { camera.fov = pose.fov; camera.updateProjectionMatrix(); }
    renderer.render(scene, camera);
    onFrame?.(smoothProgress);
    if (!paused && smoothProgress !== progress) frame = requestAnimationFrame(draw);
    else lastTime = 0;
  }

  /** 防止重复的动画循环，后台和离屏状态只在必要时刷新。 */
  function requestDraw() {
    if (!frame && !disposed && !contextLost && inView && !document.hidden) frame = requestAnimationFrame(draw);
  }

  const resize = new ResizeObserver(() => {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const nextCompact = width <= 900;
    if (nextCompact !== compact) {
      compact = nextCompact;
      cameraPath = createCameraPath(compact);
      smoothProgress = progress;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, compact ? 1.25 : 1.4));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    requestDraw();
  });
  const visibility = new IntersectionObserver(([entry]) => {
    inView = entry.isIntersecting;
    if (!inView) { cancelAnimationFrame(frame); frame = 0; lastTime = 0; }
    smoothProgress = progress;
    requestDraw();
  });
  resize.observe(canvas);
  visibility.observe(canvas);
  /** 后台恢复直接落在当前滚动位置，避免补播离开期间的镜头。 */
  function resume() {
    cancelAnimationFrame(frame);
    frame = 0;
    lastTime = 0;
    smoothProgress = progress;
    requestDraw();
  }
  /** 显卡上下文失效时交回静帧和正文，恢复后沿用当前章节。 */
  function onContextLost(event) {
    event.preventDefault();
    contextLost = true;
    cancelAnimationFrame(frame);
    frame = 0;
    onFailure?.('三维画面暂时中断，已显示静态画面，正文仍可继续浏览。');
  }
  function onContextRestored() {
    contextLost = false;
    onFailure?.(null);
    resume();
  }
  document.addEventListener('visibilitychange', resume);
  canvas.addEventListener('webglcontextlost', onContextLost);
  canvas.addEventListener('webglcontextrestored', onContextRestored);

  /** 离开样片时释放模型、原贴图和渲染资源。 */
  function dispose() {
    disposed = true;
    cancelAnimationFrame(frame);
    resize.disconnect();
    visibility.disconnect();
    document.removeEventListener('visibilitychange', resume);
    canvas.removeEventListener('webglcontextlost', onContextLost);
    canvas.removeEventListener('webglcontextrestored', onContextRestored);
    gltf.scene.traverse(node => { if (node.isMesh) node.geometry.dispose(); });
    [...originals, ...glassMaterials, ...originalTextures].forEach(resource => resource.dispose());
    studio.dispose();
    backdropTexture.dispose();
    renderer.dispose();
  }
  await renderer.compileAsync(scene, camera);
  return {
    /** 控制层传入统一进度，渲染层不读取页面滚动。 */
    update(value, staticMode) {
      progress = value;
      paused = staticMode;
      if (!initialized || paused || !inView || document.hidden) smoothProgress = progress;
      initialized = true;
      requestDraw();
    },
    /** 本地诊断读取真实帧间隔和 renderer 计数，不代表手机真机成绩。 */
    metrics() {
      const sorted = [...frameIntervals].sort((a, b) => a - b);
      return {
        exact: progress, rendered: smoothProgress, frames: sorted.length,
        medianMs: sorted[Math.floor(sorted.length * 0.5)] ?? null,
        p95Ms: sorted[Math.floor(sorted.length * 0.95)] ?? null,
        calls: renderer.info.render.calls, triangles: renderer.info.render.triangles,
        textures: renderer.info.memory.textures, programs: renderer.info.programs.length,
        dpr: renderer.getPixelRatio(), camera: camera.position.toArray(),
      };
    },
    /** 由真实 WebGL 帧导出加载兜底图，不包含叠在 canvas 上的网页文字。 */
    snapshot() {
      renderer.render(scene, camera);
      return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('静帧导出失败。')), 'image/png'));
    },
    /** 仅由本地诊断按钮触发，用于验证真实 context loss 降级路径。 */
    loseContext() { renderer.forceContextLoss(); },
    dispose,
  };
}
