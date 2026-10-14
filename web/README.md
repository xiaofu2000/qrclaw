# QRClaw Web UI

此目录是 QRClaw 的 React/Vite 前端。先在项目根目录运行 `uv run qrclaw serve`，再运行：

```bash
npm ci
npm run dev
```

开发服务器将 `/api` 请求代理到 `http://127.0.0.1:8765`。也可以在项目根目录执行 `./start_ui.sh` 同时启动两者。

检查前端：`npm run lint`、`npm run build`。

顶部小精灵使用 [bloub](https://github.com/jeremy-prt/bloub) 的 SVG 动画引擎，依据当前会话的真实状态显示：等待（小点）、思考（三点）、工具执行（轨道）、子任务（粒子）、回复（睁眼）、上下文压缩（彗星）、完成（通知点）、异常（感叹号）。当前会话空闲时会提示其他会话的后台任务。减少动态效果时显示代表帧，页面隐藏时暂停动画。

状态映射位于 `useChatWorkspace.ts`，React 视图为 `AgentSprite.tsx`；引擎版本与许可见 `src/vendor/bloub/README.md`。代码使用 MIT 许可，上游角色形象的设计权利不包含在代码许可中。
