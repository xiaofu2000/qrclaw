# QRClaw 可视化客户端

React + TypeScript 客户端，按 MVVM 分层接入本地 FastAPI 运行服务。

## 启动

先启动后端：

```bash
QRCLAW_ACCESS_TOKEN=dev-token python -m qrclaw.server.cli
```

再启动前端：

```bash
npm install
npm run dev
```

首次打开页面时，在“工作台设置”中输入同一个本地访问令牌。开发服务器会把 `/api` 和 WebSocket 代理到 `127.0.0.1:8765`。

也可以在 `web/.env.local` 中配置：

```text
VITE_QRCLAW_ACCESS_TOKEN=dev-token
VITE_QRCLAW_API_BASE_URL=/api/v1
```

生产部署默认使用同源 `/api/v1` 和 `/api/v1/events`；如需分离部署，可额外配置 `VITE_QRCLAW_EVENTS_URL`。

## 分层

```text
views / components → viewmodels → repositories → services
                              ↘ models
```

- View 只渲染状态和转发操作；
- ViewModel 管理页面状态和用户命令；
- Repository 负责快照、事件去重和断线恢复；
- Service 封装 HTTP 与 WebSocket；
- 网络 DTO 和页面领域 Model 分离。

## 验证

```bash
npm run build
npm run lint
```

---

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.
