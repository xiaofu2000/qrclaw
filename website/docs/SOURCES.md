# 素材与内容来源

核对日期：2026-10-20。

## 产品依据

- 仓库根目录 `README.md`、`PRODUCT.md`、`DESIGN.md`、`web/README.md`。
- GitHub 演示版本：`dev-webui`，提交 `e1e261ed650f023c21af93283e4b359c62efbbd5`。
- 实际任务、记忆写入、跨会话读取与安装核对见 `demo-evidence.md`。
- 所有客户与项目材料均为专门编写的虚构样例，不包含私人会话或业务资料。

## 八个网站的实际使用

| 网站 | 实际作用 | 是否引入其素材或服务 |
| --- | --- | --- |
| Scrolltide | 参考滚动驱动叙事、视差和分阶段解释方式 | 没有复制模板或付费提示词 |
| Mobbin | 参考真实界面与流程展示的内容方法 | 未下载第三方产品截图，网站使用 QRClaw 原生截图 |
| Fontshare | Satoshi 400 / 700 品牌字体 | 已下载官方 WOFF2，并在官网本地托管 |
| Coolors | 围绕既有钴蓝规划深色、浅色、中性色层次 | 未调用账号服务；网站使用自定义色值 |
| Spline | 参考浏览器可交互 3D 的表现形式 | 未嵌入 Spline 托管场景，采用自制 Three.js 场景 |
| LottieFiles | 评估轻量状态动画 | 未引入，使用原生 CSS 与已有产品演示 |
| Three.js Journey | 评估网页 3D 的知识与表现方向 | 未购买或复制课程；实现以 Three.js 官方文档为依据 |
| 3dicons | 评估立体素材风格 | 未引入，避免与自制 Q 形结构及真实界面混用 |

这是对参考、实际素材和运行依赖的区分，不把“看过网站”写成“已接入服务”。

## 运行依赖

- Three.js `0.186.1`，从官方 npm 包取得；MIT 许可证在 `site/assets/vendor/three-LICENSE.txt`。
- esbuild `0.28.2` 仅用于构建。`src/orbit.js` 为维护源文件，`site/scripts/orbit.js` 为压缩后部署文件。
- 重设计的 Q 形分层外壳、核心、引脚、槽线与粒子由本项目程序化生成。Three.js RoomEnvironment 提供环境反射，RoundedBoxGeometry 提供圆角部件；没有使用第三方模型或 AI 生成的产品截图。
- `scene-state.js` 的分段停顿与三次缓动方法改编自 [Keyframe Scroll Odyssey](https://github.com/sebastianvasquezechavarria1234/scroll-driven-camera-three.js) 的 `remapProgress` / `easeInOutCubic`，保留 [MIT 原文](../site/assets/vendor/camera-LICENSE.txt)。只采用该方法，不采用其建筑模型、镜头坐标或样式；未引入 GSAP、Lenis 新运行依赖。
- Codrops 折叠盒、3DCarousel 与 Curve Gallery 为已实际操作的设计/机制参考；未复制这些示例的图片或整页代码。具体取舍见 `REFERENCE-RESEARCH.md`。
- 字体来源：`https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700,900&display=swap`。实际下载 400 与 700 两种字重。许可入口：`https://www.fontshare.com/licenses`。
- Q 字标为本次绘制的基础圆环与斜线几何标识，不采用 Grok 角色作为原创商标。

## 产品截图

四张截图均由 `cua_repl` 从隔离演示服务 `http://127.0.0.1:8876/` 截取，视口 1440 × 1000。

| 文件 | 内容 |
| --- | --- |
| `product-task.webp` | 原始任务要求与文件工具结果 |
| `product-process.webp` | 同一会话打开执行过程面板 |
| `product-result.webp` | 实际任务摘要与继续保存偏好的对话 |
| `product-memory.webp` | 实际 Wiki 页面与两条有效来源 |

PNG 原始截图保留在本地 `.local/screenshots/`，发布版仅做 WebP 格式压缩。每张 WebP 的旁侧 JSON 保存来源，未修改界面正文。

浏览器补充观察：记忆页面实际正常显示标题和四条列表，没有显示原始 JSON 中记录的字面量换行；此结论只适用于已截图的这一页。原始记录和模型输出边界仍保留。

社交分享图 `social.png` 是官网本身的浏览器截图，来源记录嵌入 PNG。

## 中文展示字体

Noto Sans SC 700 来源为 Google Fonts 官方 CSS/字体服务，按当前官网中文文本取字符子集，文件 `site/assets/fonts/noto-sans-sc-headings.ttf`，102836字节；SIL OFL1.1 原文在同目录 `noto-sans-sc-OFL.txt`。仅用于 h1/h2，正文保持原有字体栈。字体文件是 TrueType，已据文件头选择扩展名、preload MIME 与 CSS format。
