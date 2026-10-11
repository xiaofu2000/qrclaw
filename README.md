# QRClaw

QRClaw 是一个本地 AI Agent。它提供交互式 CLI、Web UI、OpenAI 兼容 API、工具调用和跨会话 Wiki 记忆。任务由路由器选择直接执行或分步执行。

## 从源码运行

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)；运行 Web UI 还需要 Node.js 和 npm。

```bash
uv sync --extra dev
uv run qrclaw
```

首次运行会创建 `~/.qrclaw/config.yaml`。在该文件的 `llm` 部分填写 `api_key`、`model` 和需要时的 `base_url`，例如：

```yaml
llm:
  api_key: "你的 API Key"
  model: "openai/deepseek-flash"
  base_url: "https://api.deepseek.com"
```

CLI 使用 `/agent`、`/session`、`/skill` 管理对应资源，输入 `exit` 退出。CLI 默认自动执行工具调用，包括 Shell 与文件写入，请仅在信任任务来源的环境中运行。

## Web UI 和 API

在源码目录运行 `./start_ui.sh`，脚本会启动本地 API 和 Web 开发服务器；打开终端显示的 Vite 地址。也可以分别运行：

```bash
uv run qrclaw serve
npm --prefix web ci
npm --prefix web run dev
```

API 默认只监听 `127.0.0.1:8765`，提供 `/health`、`/v1/models`、`/v1/sessions`、`/v1/chat/completions`。聊天接口使用模型名 `qrclaw-agent`，目前仅支持非流式响应。Web UI 开发服务器通过 `/api` 代理到本地 API。

如果需要让 API 监听非本机地址，先设置 `QRCLAW_API_TOKEN`；远程请求须携带 `Authorization: Bearer <token>`。API 聊天也会自动执行工具调用，应妥善保管 token。通过反向代理开放服务时，应在代理处设置访问控制。

兼容旧的 OpenWebUI 启动方式：`uv run python api_server.py` 在 `127.0.0.1:8000` 提供同一 API，并继续使用 `api-server` Agent 的原有数据目录。将 OpenWebUI 的 OpenAI 兼容接口地址设置为 `http://127.0.0.1:8000/v1`，模型选择 `qrclaw-agent`。

## 工作空间与沙箱

Agent 数据存放在 `~/.qrclaw/agents/<agent-id>/`。默认 Agent 不启用 Docker 沙箱；其他 Agent 依配置启用沙箱。启用时需要 Docker，容器启动或执行失败会返回错误，不会改在宿主机执行；文件工具限制在对应工作空间内。CLI 和本机 API 的自动执行设置与沙箱开关是两件独立的事。

## 检查

```bash
uv run --no-sync pytest -q
npm --prefix web run lint
npm --prefix web run build
```

开源协议见 [LICENSE](LICENSE)。
