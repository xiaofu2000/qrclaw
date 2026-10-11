"""兼容旧的 OpenWebUI 启动命令，统一使用 qrclaw.server 应用。"""

from qrclaw.server.app import create_app
from qrclaw.workspace import Workspace

app = create_app(Workspace(agent_id="api-server"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
