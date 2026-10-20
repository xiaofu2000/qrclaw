# QRClaw 官网

Primary target: site/index.html
Related targets: site/styles, site/scripts, src
Mode: Persuade
Build path: code（沿用已记录的选择）

用户接受 AH 原模型的玻璃大脑样片，要求小幅提亮、安装并应用成熟运镜 skill、接入正式官网。正式首页用真实大脑替换旧 Q 几何体，保留产品事实、真实截图、安装入口。官网独立于客户端。

## Direction contract

THESIS: 从想法到真实工作。同一个玻璃大脑经过完整轮廓、侧面和近景，最后后退，让真实工作台接过叙事。

OWN-WORLD: 深蓝暗场、冰蓝玻璃和受控反射；AH 原始褶皱承担细节。中文 QRDisplay 与 Satoshi 字标、清晰的产品文字和真实截图，保留已认可的玻璃体积，小幅增加曝光和环境反射。

STORY: 交代任务，看到拆解与工具接力，理解记忆可追溯，再查看真实示例并获取源码。三维结构明确是工作原理示意。

FIRST VIEWPORT: 顶部固定导航；左侧两行标题“让想法，开始执行。”、一句简介与一个文字行动；右侧玻璃大脑。底部四个序号与暂停图标；后续相机沿连续小弧线进入不同构图，最后拉远衔接真实界面。平板和手机保留上方正文与下方操作区。

FORM: 用户明确选定的 AH 大脑与 Aether 冷蓝玻璃方向优先，不重新打开设计选择。Signature interaction: 首屏保持原生滚动，指数平滑进度统一控制正文、选中标签、位置/视线曲线、灯光和工作台交接。原生滚动可倒退，按钮可跳阶段；减少动态效果显示静态关键帧。已安装并应用 MengTo/Skills 的 build-threejs-scroll-worlds，固定版本、镜头账本与验证预算见 docs/brain-home-motion.md。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

当前记录：正式首页已接入 AH 玻璃大脑；独立终审查看 16 帧及运镜录屏，视觉要求满足。设计系统与 sidecar 已按最终 CSS 层叠、共享场景和独立窄屏镜头同步；证据位于 `.impeccable/review/home-brain/`。主任务记录 10 项测试和 check 通过；发布状态由实际部署结果另行确认，手机尺寸视口记录不代表手机硬件验证。

终审结论（2026-10-20）：ship。旧设计文档已同步，Persistence 修复 resolved，无剩余阻断项。

此前提亮版本：滚动轨道 540svh / 500svh，阻尼 3.2；主光、轮廓光、柔边聚光和反射环境随平滑镜头朝向旋转。该版本证据在 `.impeccable/review/slow-light/`。

快滑修复：正文统一读取实际渲染进度，横移阶段让出画面；首屏和后续章节已精简。480 帧浏览器时序回归无错位，三种尺寸 12 张端点及中间帧已检查。证据：`.impeccable/review/sync-transition/`。

当前滚动修复：删除手势拦截，恢复整页原生滚动；轨道 720svh / 680svh。桌面与手机尺寸完整往返、快速反向、暂停/减少动态和主要交互已检查；91 次输入无阻止。证据：`.impeccable/review/native-scroll/` 与 `docs/SCROLL-VALIDATION.md`。
