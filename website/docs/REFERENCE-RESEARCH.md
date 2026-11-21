# 官网重设计参考调研

日期：2026-10-20

用户反馈：首版普通、不够炫酷。以下保留两轮调研记录；随后用户已授权实施，现已完成重设计和独立审阅修正。当前结果见 DESIGN.md 与 VALIDATION-REDESIGN.md，首版技术检查不代表视觉方向得到认可。

## 公开预览中实际看到的内容

以下观察来自浏览器中播放的公开预览录像和页面说明。没有获取付费模板源码，也没有把录像当成已操作过的完整交互网站。

| 案例 | 观察 | 用在 QRClaw 的方式 | 取舍 |
| --- | --- | --- | --- |
| [Scrolltide / Camera](https://www.scrolltide.co/templates/camera) | 冷色玻璃质感的相机被拆成镜片、机身等部件；不同阶段解释不同结构，最后重新组装。预览中已看到拆解和收拢状态。 | 让一个有 QRClaw 特征的主体拆成计划、工具、结果和记忆模块；每次滚动都有明确的结构变化。 | 主参考：结构变化与叙事顺序。需要原创主体，不能直接把相机替换为几个随意漂浮的方块。 |
| [Scrolltide / Apogee](https://www.scrolltide.co/templates/apogee) | 同一个地球场景在连续镜头中改变尺度和位置，光照随章节变化；官方说明为滚动驱动四段镜头。 | 同一场景连续推进，镜头从整体进入执行细节，再退回最终结果；文字在镜头稳定处出现。 | 辅助参考：镜头和转场。地球及航天题材与产品无直接关系，不照搬主题。 |
| [Mobbin / Poly](https://mobbin.com/explore/sections/80ecdb0b-349e-4a7c-a357-5674e18c8542) | 公开短录像展示桌面、笔记本、周围物件构成的完整场景，真实文件浏览器界面位于笔记本屏幕内。 | 动画最终进入真实 QRClaw 工作台，使执行过程与实际软件界面衔接；产品界面成为场景主体。 | 主参考：产品和场景的关系。公开短录像不足以证明完整滚动路径。 |
| [Scrolltide / Astera](https://www.scrolltide.co/templates/astera) | 黑白粒子构成空间环，形态随阶段改变，文字与横向内容进入同一画面；官方说明包含散点聚合为星座。 | Wiki 段可将本次任务产生的信息聚合成可识别的记忆节点，再展示实际记忆页面。 | 局部候选；避免又做成整页只转动一个发光环。 |
| [Mobbin / ToDesktop](https://mobbin.com/explore/sections/a0994eea-b48e-48b5-8352-45dcb846e331) | 紫黑背景、居中大标题、主次按钮、下方产品窗口与连线。 | 可参考按钮层次和产品窗口的清晰度。 | 不作为整页风格主参考，构图仍接近用户不满意的常见软件官网。 |

另外看过 Synapse 的公开预览；它的卡片空间和紫色背景可以提供局部灵感，但不足以解决 QRClaw 缺少独特主体的问题，不列为主参考。

## 建议的新方向：看见一次任务如何发生

以 Camera 的结构展开为主，结合 Apogee 的连续镜头、Poly 的真实产品呈现。以下是设计提案，尚未实现。

1. **唤醒**：首屏主体占据画面大部分空间。采用有明确轮廓的原创 Q 形装置或执行结构，保留产品名称、简短用途与开始入口。
2. **拆解**：滚动推动主体展开，出现一项具体示例任务及计划、文件、工具等模块；各模块的标签与真实产品能力对应。
3. **执行**：镜头进入局部，展示动作发生与结果返回。动画回放明确标为演示，避免把特效误认为实时连接了用户 Agent。
4. **留下记忆**：结果中的可复用信息汇聚到记忆节点，衔接 Wiki 的来源、查看和编辑能力。
5. **进入产品**：三维构图收拢为真实 QRClaw 界面，接上实际任务回放和安装入口。

构图应有明显的远近、大小和明暗变化。首屏的主体、字体和配色先一起确定，不能靠在普通左右分栏上增加特效来完成重设计。

## 下一步的可检查成果

- 先制作首屏、拆解状态、进入产品三个关键画面，验证构图本身是否有识别度。
- 将三个画面连成一段可滚动的动效样片，用实际浏览器效果判断转场是否成立。
- 样片方向确定后再扩展完整页面；复用已有真实产品素材、安装说明和可访问性处理。
- 移动端保留同一叙事，减少同时出现的模块；减少动态效果模式提供静态关键画面与完整内容。

## 访问和素材边界

- Mobbin `/discover` 在当前未登录状态跳回首页，但公开 `/explore/sites` 及上述案例详情可以访问。不能据此声称已经研究了登录后的完整素材库。
- 没有购买 Scrolltide 提示词、模板或 Mobbin 订阅。付费模板不是当前建议的实施依赖。
- 本地研究截图位于 `website/.local/references/`，仅用于这次对比，不作为 QRClaw 官网对外素材。
- 尚未测试这些第三方案例的源码、移动端性能和完整实时交互。

## 免费替代来源（第二轮调研）

用户要求进一步寻找免费实现。已实际操作下面前三项在线 Demo，并读取其仓库 LICENSE。它们是可用于改造的动效示例，不是已经完成的 QRClaw 官网；没有证据证明 Scrolltide 从这些项目复制了内容。

| 方案 | 在线效果 | 源码和许可证 | 实际检查与适用位置 |
| --- | --- | --- | --- |
| On-Scroll Folding Cardboard Box | [Demo](https://tympanus.net/Tutorials/OnScrollFoldingCardboardBox/) | [源码](https://github.com/uuuulala/Threejs-folding-cardboard-box-tutorial)、[MIT](https://github.com/uuuulala/Threejs-folding-cardboard-box-tutorial/blob/master/LICENSE) | 实际滚动看到平面纸板折成盒体。可借用分部件、分阶段展开和收拢的方法，设计 QRClaw 自己的结构；纸盒外观不作为品牌方向。 |
| Keyframe Scroll Odyssey | [Demo](https://scroll-driven-camera-three-js.vercel.app/) | [源码](https://github.com/sebastianvasquezechavarria1234/scroll-driven-camera-three.js)、[MIT](https://github.com/sebastianvasquezechavarria1234/scroll-driven-camera-three.js/blob/main/LICENSE) | 实际滚动从整体建筑推进到屋顶局部，镜头与模型角度共同变化。适合参考连续镜头的关键帧组织。示例排版较粗糙，不照搬；Littlest Tokyo 模型另有 CC Attribution 署名要求，本方案采用自有场景。 |
| On-Scroll 3D Carousel | [Demo](https://tympanus.net/Development/3DCarousel/) | [源码](https://github.com/codrops/3DCarousel)、[MIT](https://github.com/codrops/3DCarousel/blob/main/LICENSE) | 实际滚动切换三维卡片组，并点击标题进入对应详情。可替换为 QRClaw 任务、执行过程和记忆界面，研究从空间画面进入产品内容的转场。 |

MIT 示例改造时保留其版权及许可声明。示例图片、模型和字体分别核对，不把代码许可证当成所有素材的统一许可。

另外两个接近前一轮方向的免费参考：

- [Curve Gallery 在线 Demo](https://tympanus.net/Tutorials/CurveGallery/) / [仓库](https://github.com/gaspoorf/curve-gallery)：已实际滚动，图像沿空间曲线分布，靠近镜头的图像放大。公开教程和源码可读，但本次未在仓库根目录及 README 发现明确许可证，暂不列为直接复制的代码来源。
- [Exploded Lens 仓库](https://github.com/wonjyou/exploded-lens)：作者说明包含九部分镜头的滚动拆解、逆向组装和程序化建模，非常接近 Camera 的技术方向。本次只核对 README 与文件目录，未本地运行，也未找到明确许可证。

补充工具来源：

- [GSAP 官方免费说明](https://gsap.com/pricing/)明确整个动画库现已免费，包含 ScrollTrigger 等插件；无需为了官网滚动编排购买此前的付费插件包。
- [React Bits](https://github.com/DavidHDev/react-bits)可作为粒子、光效和文字组件候选，其[当前许可证](https://github.com/DavidHDev/react-bits/blob/main/LICENSE.md)是 MIT + Commons Clause，允许用于商业网站，但限制单独销售、再分发组件或工具。本轮未逐项运行它的组件。

实施建议：以已核实许可的连续镜头、部件折叠和三维转场示例为起点，复用官网已有 Three.js 与真实产品素材。将工程能力转化为原创 QRClaw 场景，不引入付费模板依赖。本轮未安装依赖、复制第三方源码到官网或重新发布；移动端、性能、完整代码质量留到选定实现后的验证阶段。

## 已落地的重设计

原创 Q 形分层结构与三维材质，四段滚动镜头，任务/工具/记忆的世界坐标标注，真实工作台衔接。实际复用的免费源码方法为 Keyframe Scroll Odyssey 的分段停顿与缓动，MIT 许可已随站点保留；未复制收费模板，也未引入第三方产品模型。
