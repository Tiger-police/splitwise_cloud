#!/usr/bin/env bash
# 设置严格模式：遇到错误立即退出
set -euo pipefail

echo "=================================================="
echo "🚀 正在启动 Splitwise Cloud Edge 调度中枢..."
echo "=================================================="

# 1. 精准定位项目根目录 (无论你在哪里执行这个脚本都能找对位置)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"

# 2. 激活全局虚拟环境
VENV_PATH="$PROJECT_ROOT/venv/bin/activate"
if [ -f "$VENV_PATH" ]; then
    echo "📦 正在激活虚拟环境..."
    source "$VENV_PATH"
else
    echo "🚨 错误: 未找到虚拟环境 ($VENV_PATH)！"
    exit 1
fi

# 3. 切换到后端目录并启动服务 (读取后端的 .env)
echo "🌐 正在拉起 FastAPI 服务与监控大屏..."
cd "$BACKEND_DIR"

if [ -f "$BACKEND_DIR/.env" ]; then
    set -a
    source "$BACKEND_DIR/.env"
    set +a
fi

if [ "${OPENWEBUI_SKIP_SIGNATURE_VERIFY:-false}" = "true" ]; then
    echo "🧪 当前处于 OpenWebUI 跳过验签模式，适用于开发联调。"
elif [ -z "${OPENWEBUI_JWT_SECRET:-}" ]; then
    echo "⚠️ 未检测到 OPENWEBUI_JWT_SECRET，OpenWebUI token exchange 接口将返回配置未完成提示。"
fi

python -m app.main
