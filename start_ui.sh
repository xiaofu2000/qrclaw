#!/bin/bash
# 启动脚本：同时启动 QrClaw API 和 OpenWebUI

set -e

QRCAW_DIR="/Users/fuqingrong/Documents/agent开发/qrclaw"
VENV_PYTHON="$QRCAW_DIR/.venv/bin/python"

echo "🚀 启动 QrClaw + OpenWebUI"
echo ""

# 1. 检查并启动 API 服务
echo "1. 检查 API 服务..."
if ! curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "   启动 API 服务..."
    cd "$QRCAW_DIR"
    $VENV_PYTHON api_server.py &
    sleep 3
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "   ✅ API 服务已启动 (http://localhost:8000)"
    else
        echo "   ❌ API 服务启动失败"
        exit 1
    fi
else
    echo "   ✅ API 服务已在运行"
fi

# 2. 检查 OpenWebUI
echo ""
echo "2. 检查 OpenWebUI..."
if [ ! -d "$QRCAW_DIR/open-webui" ]; then
    echo "   ❌ OpenWebUI 未安装"
    echo "   请先运行: cd $QRCAW_DIR && git clone https://github.com/open-webui/open-webui.git"
    exit 1
fi

if [ ! -d "$QRCAW_DIR/open-webui/node_modules" ]; then
    echo "   📦 安装依赖..."
    cd "$QRCAW_DIR/open-webui"
    npm install
fi

# 3. 启动 OpenWebUI
echo ""
echo "3. 启动 OpenWebUI..."
echo "   构建中..."
cd "$QRCAW_DIR/open-webui"
npm run build 2>&1 | tail -5

echo ""
echo "🌐 启动 OpenWebUI 服务..."
echo ""
echo "配置步骤："
echo "1. 浏览器打开 http://localhost:3000"
echo "2. 注册/登录账号"
echo "3. 左下角头像 → Settings → External Connections"
echo "4. 添加 OpenAI API:"
echo "   - URL: http://localhost:8000/v1"
echo "5. 新建对话，选择模型 'qrclaw'"
echo ""

npm start
