---
name: "QRClaw 官网"
description: "在深蓝暗场中，以 AH 玻璃大脑与连续镜头衔接真实工作台。"
colors:
  blue: "#aac9ff"
  blue-hover: "#d3e0ff"
  ink: "#eef5ff"
  muted: "#aebfd4"
  paper: "#030916"
  soft: "#0c1524"
  line: "#30425c"
  night: "#030916"
  phase-line: "#b6dcff"
  phase-number: "#c2dfff"
  extension: "#264aca"
  panel: "#202930"
  node: "#25313c"
  terminal: "#141c24"
  terminal-text: "#e0eaff"
  light-bg: "#dfe5e9"
  light-ink: "#18232c"
  light-muted: "#4d6172"
  light-blue: "#2449a9"
  light-blue-hover: "#142c71"
  light-line: "#afbec9"
  focus: "#78a0ff"
typography:
  display:
    fontFamily: "QRDisplay, Satoshi, sans-serif"
    fontSize: "clamp(42px, 4.8vw, 70px)"
    fontWeight: 700
    lineHeight: 1.16
    letterSpacing: "-.04em"
  headline:
    fontFamily: "QRDisplay, Satoshi, sans-serif"
    fontSize: "clamp(32px, 3.6vw, 52px)"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-.03em"
  scene-heading:
    fontFamily: "QRDisplay, Satoshi, sans-serif"
    fontSize: "clamp(34px, 3.6vw, 54px)"
    fontWeight: 700
    lineHeight: 1.24
    letterSpacing: "-.035em"
  body:
    fontFamily: 'Satoshi, "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.85
  action:
    fontFamily: 'Satoshi, "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "14px"
    fontWeight: 700
  label:
    fontFamily: 'Satoshi, "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "12px"
    fontWeight: 400
  code:
    fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace'
    fontSize: "12px"
    lineHeight: 1.9
rounded:
  control: "4px"
  tag: "5px"
  node: "9px"
  terminal: "10px"
  surface: "12px"
spacing:
  inline-small: "8px"
  inline-medium: "12px"
  node-gap: "10px"
  action-gap: "28px"
  section-desktop: "124px"
  section-mobile: "80px"
components:
  button-white:
    backgroundColor: "#edf1f4"
    textColor: "#101315"
    typography: "{typography.action}"
    rounded: "{rounded.control}"
    padding: "0 23px"
  button-white-hover:
    backgroundColor: "#dce7ff"
  text-link:
    textColor: "{colors.blue}"
    typography: "{typography.action}"
    padding: "8px 0"
  text-link-hover:
    textColor: "{colors.blue-hover}"
  text-link-light:
    textColor: "#e8efff"
    padding: "8px 0"
  nav-action:
    textColor: "#edf1f4"
    rounded: "{rounded.control}"
    padding: "11px 17px"
  memory-node:
    backgroundColor: "{colors.node}"
    textColor: "{colors.ink}"
    rounded: "{rounded.node}"
    padding: "10px 17px"
  memory-node-active:
    backgroundColor: "{colors.blue}"
    textColor: "#101828"
  memory-tag:
    backgroundColor: "{colors.soft}"
    textColor: "#a5bfff"
    typography: "{typography.label}"
    rounded: "{rounded.tag}"
    padding: "5px 9px"
  memory-note:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.surface}"
    padding: "28px 32px"
  terminal:
    backgroundColor: "{colors.terminal}"
    textColor: "{colors.terminal-text}"
    typography: "{typography.code}"
    rounded: "{rounded.terminal}"
  phase-control:
    textColor: "#a2b0bc"
    padding: "13px 0"
---

# Design System: QRClaw 官网

## Overview

**Creative North Star: "冷蓝玻璃暗棚"**

深蓝暗场承托 AH 的真实 Human Brain 模型。自然褶皱、冰蓝透射和窄条反射构成体积；固定模型与连续相机把完整轮廓、侧面和近景连接起来，最后让真实工作台接过叙事。

中文标题、明确的行动入口与四阶段定位保持可读。正文保留既有产品回放、记忆、扩展和安装组件，仅共享更新后的暗蓝基础色；真实截图保留产品自身外观。

**Key Characteristics:**

- 深蓝暗场、冰蓝玻璃、受控反射与白色文字。
- 同一个固定模型、连续镜头、四阶段自然滚动。
- QRDisplay 中文标题、Satoshi 品牌与正文、等宽代码。
- 矩形行动、细线导航、真实截图和可追溯模型署名。
- 暂停与减少动态模式保留章节阅读和静态关键镜头。

范围仅为 `website/`；根目录工作台 `DESIGN.md` 与 `PRODUCT.md` 不变。方向见 [.impeccable/surfaces/home.md](.impeccable/surfaces/home.md)。实现以 `site/index.html`、先加载的 `site/styles/main.css`、后覆盖的 `site/styles/brain-home.css`、`site/scripts/app.js`、`src/brain-scene.js` 与 `src/brain-camera.js` 为准。

## Colors

基础色以 frontmatter 和 CSS 最终层叠值为准，玻璃材质色不等于界面主题色。

### Primary

- **冰蓝 `blue` / 高亮冰蓝 `blue-hover`**：正文链接、回放、入口选中态及记忆节点。
- **钴蓝 `extension`**：保留的开放能力章节背景。
- **阶段冰蓝 `phase-line` / `phase-number`**：首屏选中阶段的下划线和序号。

### Neutral

- **深夜蓝 `paper` / `night`、暗蓝 `soft`**：整页与记忆章节基础面。
- **冰白 `ink`、灰蓝 `muted`、结构线 `line`**：正文、辅助说明与分隔。
- **深钢灰 `panel` / `node`**：既有记忆正文和节点表面；终端保留独立深色代码面。
- **安装浅底 `light-bg` 与 `light-*`**：安装区局部主题，保留原来的深色文字与深蓝链接。
- **焦点蓝 `focus`**：键盘焦点描边（3px，外偏移 5px）。

**The 状态可读 Rule.** 阶段与选中状态同时保留文字、下划线和可访问语义。

Sidecar 的八阶色带仅为设计面板的派生展示元数据，不是新增运行时色板。主按钮与导航部分颜色使用源码中的固定值，不随基础 `ink` / `paper` 自动改变。

## Typography

**Display Font:** 本地 QRDisplay（Noto Sans SC 700 标题字符子集）；官网 h1、h2 使用 `QRDisplay, Satoshi, sans-serif`。

**Body / Brand Font:** 本地 Satoshi 400/700；中文正文回退 PingFang SC、Microsoft YaHei、sans-serif。字体均采用 `font-display: swap`。

**Code Font:** SFMono-Regular、Consolas、Liberation Mono、monospace。

- **Display**：桌面值见 frontmatter；≤900px 为 `clamp(33px,5.8vw,49px)`，≤720px 为 `clamp(31px,7.5vw,45px)`。宽度 ≥901px 且高度 ≤760px 时为 46px。
- **Scene heading**：桌面值见 frontmatter；≤900px 为 34px，≤720px 为 30px、行高 1.25；宽度 ≥901px 且高度 ≤760px 时为 40px。
- **Headline**：普通章节沿用既有标题层级；≤720px 为 34px。安装和 FAQ 保留各自尺寸。
- **Body**：基础 16px、段落行高 1.85；场景说明为 15px、行高 1.9，≤900px 为 14px / 1.7，≤720px 为 13px。
- **Action / Label / Code**：正文行动 14px/700；首屏行动 13px，≤720px 为 12px。小标签、来源和代码按实际组件使用 11–12px，不作为正文默认值。

**The 标题子集 Rule.** 新增中文标题时复核并更新 QRDisplay 子集。

## Layout

自然滚动，不拦截滚轮或触控。主容器 `min(1248px, calc(100% - 112px))`；常规章节上下留白 124px，≤720px 为 80px。正文保留三列回放步骤、不对称双列和手机单列布局；图片保持 `object-fit: contain`，代码在自身容器内滚动。

首屏轨道桌面高 540svh，≤900px 高 500svh；舞台 sticky、100svh、无最小高度限制。画布铺满舞台，居中标题在上方 15%。后续桌面文案位于左侧或右侧 7%，第三阶段在上方 22%。底部阶段、署名和动态控制持续定位。

| 条件 | 实际变化 |
| --- | --- |
| 默认桌面 | 固定导航高 82px；场景文案宽 31%，记忆文案宽 30%；真实 UI 桥接位于上方 25%、右侧 6%、宽 51%。 |
| ≤1100px | 正文主容器左右 32px、导航左右 28px；压缩正文列距。 |
| ≤900px | CSS 场景与相机同时进入 compact 构图；文案顶部 112px、左右 28px；桥接图顶部 43%，最大高 36svh。 |
| ≤720px | 导航 72px 与折叠菜单；正文容器左右 20px；场景文案顶部 98px、左右 24px；桥接图左右 20px；正文双列转单列。 |
| 高度 ≤760px 且宽度 ≥901px | 首屏标题顶部 14%，场景文案顶部 25%，压缩标题与行动间距。 |

窄屏使用独立的相机位置、视线与 FOV 端点，给上方文字和底部控制留空间，不按桌面镜头统一乘距离系数。完整长截图不能替代粘性舞台的逐阶段检查。

## Elevation & Depth

深度来自原模型褶皱、实际 WebGL 暗场、双面玻璃与四条棚灯反射。灯组和反射环境跟随相机的平滑朝向绕模型转动；方向主光强度 2.4、轮廓光 2.3，另加强度 22、柔边 .7 的聚光照亮当前可见褶皱。ACESFilmic 曝光 1.18，环境反射强度 1.32。材质保留 `roughness .13`、`transmission 1`、`thickness .9`、`ior 1.42`、`opacity .68`、`DoubleSide`、`depthWrite false` 与原法线贴图的 `.22` 强度；包含透明混合，不描述成纯物理透射。

**The Dark Refraction Rule.** 透射背景必须存在于 WebGL 场景内，CSS 暗底不能代替玻璃采样背景。

- **真实产品窗口**：`0 30px 90px #0005`。
- **场景 UI 桥接**：`0 24px 60px #0007`。
- **记忆节点 / Wiki 字标**：`0 8px 24px #0002` / `0 15px 30px #0003`。
- **展开截图 / 复制反馈**：`0 20px 50px #0003` / `0 8px 24px #0b172730`。

记忆正文无阴影。普通正文组件继续通过表面颜色与细线分层。

## Shapes

大脑保留 AH 原始 GLB 几何与自然轮廓，不添加替代几何、拟人面部或部件标签。主要行动与导航入口为 4px 圆角矩形；记忆节点 9px，终端和桌面桥接图 10px，产品窗口与记忆正文 12px。手机产品窗口为 8px、桥接图为 7px。

图标使用内联描边 SVG；既有记忆类别的 `Aa` 与 `{ }` 作为文字符号保留，不扩展成通用图标库。

## Components

### Actions and Fixed Navigation

银白按钮沿用固定的浅底深字，常规最小高 50px、手机 48px；首屏覆盖为 44px、内边距 10px 20px。悬停上移 3px。导航在深蓝透明渐变上，手机菜单使用独立暗蓝底；Escape 收起后回到菜单按钮。跳转主内容链接在聚焦时出现。

### Four-stage Brain

阶段为唤醒、拆解、执行、记忆，按钮跳到同一滚动坐标的 `0 / 1/3 / 2/3 / 1`。`sampleTimeline` 每段首尾各保留 12% 阅读停顿，中间 76% 缓动。精确滚动状态控制正文、导航和可访问属性；平滑渲染进度只控制相机和工作台交接。

| 阶段 | 当前构图 |
| --- | --- |
| 唤醒 | 完整三分之四轮廓，居中标题与开始入口。 |
| 拆解 | 小弧线进入侧面，为任务说明留出侧方空间。 |
| 执行 | 从另一侧靠近褶皱与透射细节，配合工具说明。 |
| 记忆 | 后退并抬高，让真实工作台与可追溯 Wiki 接过叙事。 |

模型归一化最大尺寸 3.2，固定位置 `(0,-.35,0)`、旋转 `(.08,-1.05,-.08)`。相机位置和视线分别沿 centripetal Catmull–Rom 曲线运动，FOV 随端点插值；数值真源为 `src/brain-camera.js`。

交接在实际渲染进度 .78–.96 显现：48px 位移、.94 到 1 缩放、透明度渐入，大脑画面最低透明度 .28。完整可操作的演示位于后续正文。

### Motion, Pause and Fallback

指数阻尼为 `1-exp(-3.2×dt)`，时间步长上限 1/30 秒。模型无呼吸、旋转或指针驱动；没有粒子、Bloom、阴影或全屏后处理。像素比上限桌面 1.7、≤900px 为 1.35。镜头到位、离屏或后台时停止持续绘制；恢复时落到当前阅读位置。

暂停和减少动态模式直接采用 `phase / 3` 关键镜头，仍更新阅读内容。CSS 颜色反馈 .2 秒、悬停 .25 秒，强调曲线 `cubic-bezier(.16,1,.3,1)`；画布就绪淡入 .35 秒。全局暂停、后台与减少效果按各自状态停止动画。

首次加载先显示当前真实场景导出的静帧，再闲时载入三维模块。加载失败或 context 丢失时回到静帧与中文状态说明，正文和导航继续可用。无 JavaScript 时隐藏阶段和暂停控件，保留内容与入口。

运镜采用已安装的 `build-threejs-scroll-worlds`：版本及来源见 [docs/brain-home-motion.md](docs/brain-home-motion.md) 和 [docs/skill-source.json](docs/skill-source.json)。`sampleTimeline` 的原有 MIT 示例归属继续保留在素材说明中。

### Product Replay, Memory and Code

真实回放初始不自动播放；开启后每 5 秒在页面前台、区域可见且未暂停/减少效果时推进。手动切换停止自动播放；步骤和入口标签支持方向键、Home、End。

记忆节点更新标题、正文、来源和标签；默认深钢灰，选中浅蓝底深色字，悬停上移 4px。正文无阴影，桌面最小高 380px，手机内边距 25px、取消最小高度。内容明确为示意，真实截图通过 details 展开。

复制中文反馈持续约 3.2 秒，失败时选中原文并提示手动复制；FAQ 使用原生 details/summary。无输入表单。状态在 `WebsiteModel`，DOM 与滚动在 `app.js`，进度采样在 `scene-state.js`，相机与渲染分别在 `brain-camera.js`、`brain-scene.js`。

### Evidence Boundary

本轮独立终审查看 16 帧截图及运镜录屏，视觉要求满足；证据在 `.impeccable/review/home-brain/`。主任务记录 10 项测试与 check 通过，本次文档同步不重复执行构建或浏览器检查。

录制期间的本地桌面浏览器、DPR 1 样本：桌面帧间隔中位数 16.7ms / p95 25.1ms，手机尺寸视口 8.8ms / p95 17.1ms。它们不是手机硬件成绩或线上指标。模型原文件 8,287,448 bytes，SHA-256 与授权见 [资产 README](site/assets/brain/README.md)；页面保留 AH 与 CC BY 4.0 署名。

## Do's and Don'ts

### Do:

- **Do** 保留 AH 原始几何、冷蓝玻璃层次与可见署名。
- **Do** 保持场景与相机的 900px compact 分支一致，并保留独立窄屏镜头。
- **Do** 以精确滚动状态控制阅读，以平滑进度控制相机与交接。
- **Do** 保留键盘、暂停、减少动态与真实模型静帧降级。
- **Do** 区分工作原理示意、真实截图、实际运行状态与本地性能样本。

### Don't:

- **Don't** 用旧 Q 几何或旋转环替代已确认的真实大脑。
- **Don't** 将模型旋转、指针偏移或额外装饰重新加入这条相机叙事。
- **Don't** 把材质色、派生色带或被覆盖的 CSS 旧值当成主题真源。
- **Don't** 将官网视觉规范覆盖到根目录产品工作台。
- **Don't** 将本地手机尺寸视口数据写成手机真机或线上性能保证。

未规范化的遗留项：`main.css` 中旧 Q 部件与已被覆盖的场景规则不属于本版视觉系统；本次仅同步文档，不清理源码。
