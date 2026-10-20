# QRClaw 官网

中文产品官网，源码、设计记录与本地验证集中在此目录。产品客户端仍位于仓库根目录的 `web/`。

## 预览与构建

```bash
cd website
npm ci
npm run build
npm test
npm run check
npm run dev
```

打开 `http://127.0.0.1:8877/`。网站的源页面目录为 `site/`，实际部署目录为构建生成的 `dist/`，可独立静态托管，不需要运行 QRClaw API。已提交构建产物时，也可以直接用 Python 静态服务器预览 `site/`。

## 维护位置

- `site/index.html`：官网结构、静态文案与安装入口。
- `site/styles/main.css`：正文基础排版与交互；后加载的 `brain-home.css` 决定首页暗蓝主题、首屏布局与响应式覆盖。
- `site/scripts/content.js`：步骤、记忆示意与使用方式数据。
- `site/scripts/model.js`：与 DOM 无关的交互状态。
- `site/scripts/scene-state.js`：四阶段镜头的进度、停顿与缓动计算。
- `site/scripts/app.js`：渲染、键盘、复制和观察器。
- `src/brain-scene.js`：共享 AH 原模型、玻璃材质、灯光、渲染与资源释放。
- `src/brain-camera.js`：桌面 / compact 镜头端点、位置与视线曲线、阻尼；场景修改后运行 `npm run build`。
- `site/scripts/scene-debug.js`：仅 `127.0.0.1` 加 `?scene-debug` 时载入的诊断面板。
- `site/assets/`：本地字体、真实产品截图、图标与素材许可。
- `docs/`：素材、实测依据及验收记录。

界面与逻辑分离。精确滚动状态控制章节和可访问属性，平滑渲染状态控制相机与工作台交接；模型固定。暂停或减少动态效果直接切换静态关键镜头，离屏、后台与镜头到位时停止持续绘制。3D 加载失败或 context 丢失时显示真实模型静帧，保留全部文字与入口。独立 `brain-preview.html` 与官网共享渲染器，但将样片 0..1 进度映射到官网 0..1/3，仅播放前两个镜头端点。

## 模型与运镜来源

使用 AH 的 Human Brain 官方 GLB（8,287,448 bytes），原文件 SHA-256、来源与 CC BY 4.0 见 [资产 README](site/assets/brain/README.md)，可见署名保留在页面中。

已安装并应用 [build-threejs-scroll-worlds](https://github.com/MengTo/Skills/tree/398cd41c86ba42fbb30e944ae507f463648f08b7/agent-skills/web-design/build-threejs-scroll-worlds)，固定版本 `398cd41c86ba42fbb30e944ae507f463648f08b7`；本地位于 `/Users/admin/.codex/skills/build-threejs-scroll-worlds/SKILL.md`。曲线、阻尼、独立窄屏镜头与渲染状态分离的应用记录见 [运镜说明](docs/brain-home-motion.md)。[来源快照](docs/skill-source.json) 中的 stars 属于仓库，不是单项 skill 效果评分。

## 本地场景诊断

```bash
npm run build:brain
npm run dev
```

打开 `http://127.0.0.1:8877/?scene-debug`，可查看 exact / rendered 进度、帧间隔与渲染计数，下载真实静帧、录制前进回退、预览减少动态或模拟 context 丢失。诊断入口仅在本地 `127.0.0.1` 启用。

本轮终审的 16 帧与录屏见 `.impeccable/review/home-brain/`；10 项测试和 check 已通过。录制期间、本地桌面浏览器 DPR 1 的帧间隔中位数 / p95 为：桌面 16.7 / 25.1ms，手机尺寸视口 8.8 / 17.1ms；后者不是手机硬件结果。

## 产品演示

截图来自真实 QRClaw 运行结果，输入为专门编写的公开虚构项目笔记。重新制作时在仓库根目录执行：

```bash
uv run --no-sync python website/tools/serve_demo.py --self-check
uv run --no-sync python website/tools/serve_demo.py
```

隔离服务在 `http://127.0.0.1:8876/`，运行数据保存在 `.local/`。首次真实模型调用需要已有模型配置；该服务不公开部署，不使用用户现有会话。详细边界见 `docs/demo-evidence.md`。

## 浏览器性能复测

```bash
node tools/prepare-browser-check.mjs
```

在浏览器打开 `/__check.html`；该临时页面只注入测量脚本，其他内容与资源和首页一致。LCP、CLS、交互事件耗时与 120 帧采样写在根元素 `data-verification-metrics` 属性中。记录浏览器、视口、网络和缓存条件，不能把本地实验室数值当成线上真实用户数据。

测量结束移除 `site/__check.html`。`npm run check` 会阻止把该临时文件一起发布。

## 发布

站点身份保存在 `.openai/hosting.json`，固定复用其中的 `project_id`。部署目录为 `dist/`，必须先构建、验证，再用 Sites 源码工作流同步和保存版本，部署对应版本。

发布使用 `.local/publish-checkout/` 下的独立副本，以免在官网目录创建嵌套 Git 仓库，或改动 QRClaw 原仓库的提交。该副本不得包含 `.local` 运行数据、`node_modules`、浏览器检查页或截图审阅缓存。公开访问范围与自定义域名属于托管配置。

当前下载说明明确使用 `dev-webui`，因为本次官网展示的工作台与记忆管理尚未全部进入仓库默认分支。变更下载入口前，应重新核对远端分支与演示版本。
