# QRClaw Web UI

此目录是 QRClaw 的 React/Vite 前端。先在项目根目录运行 `uv run qrclaw serve`，再运行：

```bash
npm ci
npm run dev
```

开发服务器将 `/api` 请求代理到 `http://127.0.0.1:8765`。也可以在项目根目录执行 `./start_ui.sh` 同时启动两者。

检查前端：`npm run lint`、`npm run build`。
