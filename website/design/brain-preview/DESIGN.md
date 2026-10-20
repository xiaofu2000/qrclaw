---
name: QRClaw 大脑视觉样片
description: AH Human Brain 冷蓝玻璃样片版式，与正式官网共享当前渲染器
colors:
  night: "#030916"
  ice-white: "#eef5ff"
  ice-blue: "#a8d9ff"
  secondary-text: "#afbbce"
  control-text: "#b3c1d5"
  control-surface: "#071020"
  control-hover: "#0c1c34"
typography:
  display:
    fontFamily: "QRDisplay, 'PingFang SC', sans-serif"
    fontSize: "clamp(32px, 4.4vw, 70px)"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-.03em"
  headline:
    fontFamily: "QRDisplay, 'PingFang SC', sans-serif"
    fontSize: "clamp(32px, 4vw, 62px)"
    fontWeight: 700
    lineHeight: 1.35
    letterSpacing: "-.03em"
  body:
    fontFamily: "Satoshi, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.9
  label:
    fontFamily: "Satoshi, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "12px"
    fontWeight: 400
  control:
    fontFamily: "Satoshi, 'PingFang SC', 'Microsoft YaHei', sans-serif"
    fontSize: "11px"
    fontWeight: 400
rounded:
  pill: "99px"
spacing:
  page-inline: "4vw"
  mobile-inline: "24px"
  inline-gap: "16px"
components:
  motion-toggle:
    backgroundColor: "{colors.control-surface}"
    textColor: "{colors.control-text}"
    typography: "{typography.control}"
    rounded: "{rounded.pill}"
    padding: "12px 16px"
  motion-toggle-hover:
    backgroundColor: "{colors.control-hover}"
  phase-navigation:
    textColor: "{colors.control-text}"
    typography: "{typography.label}"
  text-link:
    textColor: "{colors.ice-white}"
    padding: "9px 0"
---

# Design System: QRClaw 大脑视觉样片

## Overview

**Creative North Star: "冷蓝玻璃暗棚"**

用户选定 AH 的 Human Brain 与 Aether 1 冷蓝玻璃方向。完整人脑的自然褶皱承担主体细节，暗场、窄条反射与短中文标题共同建立空间感。

本文件管理独立两阶段样片的版式；正式官网已接入同一个 AH 模型与玻璃方向，其四阶段布局由 `../../DESIGN.md` 和 `../../.impeccable/design.json` 管理。两者共享当前 `src/brain-scene.js` 与 `src/brain-camera.js`，不是彼此冻结的旧版本。样片构图见[表面契约](../../.impeccable/surfaces/brain-preview.md)。

**Key Characteristics:**
- 深蓝黑暗场、冰蓝反射与白色文字。
- 真实模型褶皱、透射与透明度共同构成玻璃体积。
- 大幅留白、短标题、细线导航与胶囊动态控制。

## Colors

### Primary
- **冰蓝**：链接悬停、焦点环和文字选区；模型的蓝色主要来自材质与灯光。

### Neutral
- **深夜蓝黑**：页面背景、选区文字与 WebGL 清屏底色。
- **冰白**：标题与主要链接。
- **次级灰蓝**：说明文字。
- **控件灰蓝、控件暗面及悬停暗面**：阶段导航和动态按钮。

**The Dark Refraction Rule.** 透射背景必须存在于 WebGL 场景内；CSS 背景不能代替玻璃采样所需的暗场。

## Typography

标题采用本地 QRDisplay（Noto Sans SC 标题子集），西文及界面文字采用本地 Satoshi。中文正文使用系统中文回退字体。

标题与正文层级以 frontmatter 为准。首屏说明为 14px；文本链接为 14px；页头链接为 13px；阶段序号及动态按钮为 11px。窄屏的二阶段标题固定 36px、正文 13px、首屏说明 12px。品牌字桌面为 26px/700、字距 -.04em，窄屏为 23px。

**The Direct Title Rule.** 标题直接进入正文主题，不增加眉题或“VISUAL STUDY / 01”装饰标签。

## Layout

滚动轨道为 290svh，内部舞台 sticky、100svh、裁切溢出。页头与舞台控制的桌面横向边距采用 page-inline；页头纵向 28px，底部控制距底 32px。首屏标题位于舞台上方 15%，居中；第二阶段正文位于左侧 8vw、上方 37%。

断点为 700px：页头边距 22px 24px；首屏标题上方 20%、横向 20px；第二阶段正文上方 16%、两侧 26px；控制距底及两侧均为 24px。窄屏隐藏滚动提示和强制换行，保留阶段入口、链接和动态开关。

## Elevation & Depth

界面没有盒阴影。深度来自原模型几何、物理玻璃、实际 WebGL 暗场和灯光。场景背景是 256×256 径向纹理：中心 `(130,172)`、半径 175，色标为 `0: #1a3765`、`.45: #08162f`、`1: #02050d`；渲染采用 ACESFilmic、曝光 1.18，设备像素比上限为桌面 1.7、画布宽度 ≤900px 为 1.35。

运行时 `MeshPhysicalMaterial`：`color #e5f3ff`、`roughness .13`、`metalness 0`、`transmission 1`、`thickness .9`、`ior 1.42`、`attenuationColor #a8daff`、`attenuationDistance 4`、`clearcoat 0`、`transparent true`、`opacity .68`、`DoubleSide`、`depthWrite false`。保留原法线贴图，`normalScale (.22,.22)`；这包含部分透明混合，不描述成纯物理透射。

反射暗棚底色为 `#020611`；四条面灯的“位置 / 尺寸 / 颜色 / 强度”分别为 `[-4,2,3] / [1.2,7] / #bddaff / 6.5`、`[4,1,0] / [.8,6] / #639fff / 3`、`[0,5,-2] / [6,1.5] / #e1edff / 6`、`[0,-3,2] / [4,.6] / #3462bd / 1.5`。PMREM 模糊参数 .04、环境强度 1.32；方向轮廓光为 `#7db5ff / 2.3 / [-4,2,-3]`，主光为 `#dcecff / 2.4 / [3,5,4]`。另有 `#eaf4ff`、强度 22、角度 π/5、柔边 .7、位置 `[-3,3,4]` 的聚光。位置相对模型中心；灯组和反射环境跟随相机的平滑朝向旋转，所有直射灯都对准模型。

## Shapes

大脑轮廓来自原始 GLB，不添加拟人面部或替代几何体。界面以无容器文字、1px 细线和胶囊控制为主；没有卡片或表单组件。

## Components

### Navigation and links

阶段入口使用文字与底线：当前阶段为白字、`#d5e7ff` 底线；序号为 `#94abc9`。阶段链接间距桌面 30px、窄屏 22px。文本链接以 1px `#526780` 底线和 20px 线性 SVG 箭头呈现；箭头间距 42px。所有链接与按钮的键盘焦点使用 2px 冰蓝轮廓、6px 外偏移。

### Motion toggle

胶囊按钮的边线为 `#35445c`，悬停为 `#99b9df`；填充与内边距见 frontmatter。按钮通过 `aria-pressed` 反馈状态，文案为“暂停动态 / 启用动态”。暂停或减少动态模式仍可滚动及访问第二阶段，并直接切换完整镜头。

### Brain stage

样片沿用正式官网的共享渲染器：模型最大尺寸归一化为 3.2，固定位置 `(0,-.35,0)`、旋转 `(.08,-1.05,-.08)`。不再执行旧版模型横移、旋转与呼吸动画。

样片滚动先由 `brainProgress` 保留首尾各 18% 停顿，中段 smoothstep 得到 `p∈[0,1]`；正文使用这个进度，向共享场景传入 `p/3`。因此样片只走正式官网的前两个镜头端点（唤醒 → 拆解），不播放完整四阶段。共享场景继续采用 `sampleTimeline` 停顿、独立位置/视线 Catmull–Rom 曲线与 3.2 指数阻尼，时间步长上限 1/30 秒。

版式断点仍为 700px；共享相机 compact 断点为画布宽度 900px，采用独立位置、视线与 FOV。暂停或减少动态模式直接显示对应端点；镜头到位、离屏或后台时不持续绘制。镜头数值统一见 `../../src/brain-camera.js`。

模型为 AH 官方下载的原始 GLB；当前文件 8,287,448 bytes，SHA-256 `1f6a19277ae876a0ccb3f63fcff5c7fe3c6039894a158cbc78c658f055445e5a`，本次文档复核与[资产 README](../../site/assets/brain/README.md)一致。原文件未改写，材质在运行时替换；页脚保留作者、原模型与 CC BY 4.0 链接。

## Do's and Don'ts

### Do:
- **Do** 保留真实模型、可见署名及原始资产证据。
- **Do** 让标题、镜头、阶段状态共享进度，并保持键盘和减少动态模式可用。
- **Do** 将移动浏览器视口检查与真实设备性能验证分开记录。

### Don't:
- **Don't** 将样片的两阶段版式当作正式官网四阶段布局，或把共享渲染器当作独立旧版本。
- **Don't** 用透明 WebGL 画布加 CSS 暗底代替真实透射背景。
- **Don't** 添加卡通表情、替代脑模型或装饰眉题。

记录范围：依据最终 HTML、CSS、场景源码与控制源码提取；sidecar 色阶仅为面板生成的展示元数据，不代表页面已使用这些颜色。未在本次文档工作中执行真机性能验证。未将残留的 `:not(.eyebrow)` 选择器视为眉题组件或设计规范。
