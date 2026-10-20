# AH 大脑视觉样片

Primary target: site/brain-preview.html
Related targets: site/styles/brain-preview.css, site/scripts/brain-preview.js, src/brain-scene.js, src/brain-camera.js
Mode: Persuade
Build path: code
Scoped visual system: [design/brain-preview/DESIGN.md](../../design/brain-preview/DESIGN.md)
Scoped sidecar: [design/brain-preview/.impeccable/design.json](../../design/brain-preview/.impeccable/design.json)

用户已选定 Aether 1 的冷蓝玻璃视觉方向，以及 AH 的 Human Brain 原模型。独立样片保留两阶段版式；该方向随后已获用户接受并接入正式官网，两者共享当前渲染器。

THESIS: 一个完整的大脑在冷蓝光中显现；镜头靠近时，文字从“想法”推进到“执行”。
OWN-WORLD: 深蓝黑背景、冰蓝反射、留白和克制的白色字体。细节来自真实模型褶皱，不添加卡通表情或几何零件。
STORY: 看见完整主体，滚动靠近细节，理解 QRClaw 从目标走向行动。
FIRST VIEWPORT: 简洁导航、居中标题、完整大脑、底部阶段入口和动态效果控制。
FORM: 用户已明确指定模型和参考方向，优先于抽签种子 9088614e / index 6。保留自然滚动与键盘阶段跳转；减少动态效果时直接切换关键镜头。
FINISH: 原模型下载、材质调校、桌面和移动浏览器验证、独立评审与记录完成后才发布。缺模型时不得用自制形状替代，不得标记完成。

模型来源：https://sketchfab.com/3d-models/human-brain-c9c9d4d671b94345952d012cc2ea7a24
作者：AH / https://sketchfab.com/agher
授权：CC BY 4.0 / https://creativecommons.org/licenses/by/4.0/
当前实现：AH 官方 GLB 原文件已接入；两阶段布局、冷蓝玻璃材质、桌面和窄屏镜头已落地。原文件的字节数、SHA-256、来源及授权见[资产 README](../../site/assets/brain/README.md)。本文件不代表已发布或已通过真机性能验证。
独立终审：ship；要求删除的页头 VISUAL STUDY / 01 标签已移除。

材质实现：背景进入 WebGL 透射缓冲，深蓝暗场和窄条灯构成反射；保留原模型几何及低强度法线贴图，以物理透射、折射率、厚度、部分透明度和双面渲染形成玻璃体积。精确参数记录在独立设计文件中。
响应式镜头：共享官网的独立 desktop / compact 相机，分界为画布宽度 900px；样片版式仍以 700px 分界。模型固定，位置与视线沿各自曲线移动；曝光 1.18、环境强度 1.32。
进度映射：样片文字与阶段进度 0..1 向共享场景传入 0..1/3，仅使用官网前两个端点；不复述旧版模型横移、旋转和呼吸参数。
范围边界：本文件记录样片版式；正式官网四阶段方向与状态见 home.md。样片页头没有 VISUAL STUDY / 01 标签，阶段序号为 11px。共享场景的后续变更应同时核对两处调用。
