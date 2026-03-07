#!/bin/bash
# IDA Service 启动脚本

# 设置默认配置
IDA_SERVICE_HOST="${IDA_SERVICE_HOST:-127.0.0.1}"
IDA_SERVICE_PORT="${IDA_SERVICE_PORT:-5000}"
IDA_DEBUG_MODE="${IDA_DEBUG_MODE:-false}"
IDA_LOG_LEVEL="${IDA_LOG_LEVEL:-INFO}"
IDA_LOG_DIR="${IDA_LOG_DIR:-$(pwd)/logs}"
IDA_DEBUG_SCRIPT_DIR="${IDA_DEBUG_SCRIPT_DIR:-$(pwd)/logs/scripts}"
IDA_DEFAULT_INPUT_PATH="${IDA_DEFAULT_INPUT_PATH:-}"

# 创建日志目录
mkdir -p "$IDA_LOG_DIR"
mkdir -p "$IDA_DEBUG_SCRIPT_DIR"

# 切换到项目根目录
cd "$(dirname "$0")/../.." || exit 1

echo "=================================================="
echo "IDA Service - 启动脚本"
echo "=================================================="
echo "Host: $IDA_SERVICE_HOST"
echo "Port: $IDA_SERVICE_PORT"
echo "Debug Mode: $IDA_DEBUG_MODE"
echo "Log Level: $IDA_LOG_LEVEL"
echo "Log Directory: $IDA_LOG_DIR"
if [ -n "$IDA_DEFAULT_INPUT_PATH" ]; then
    echo "Default Input: $IDA_DEFAULT_INPUT_PATH"
fi
echo "=================================================="
echo ""

# 导出环境变量
export IDA_SERVICE_HOST
export IDA_SERVICE_PORT
export IDA_DEBUG_MODE
export IDA_LOG_LEVEL
export IDA_LOG_DIR
export IDA_DEBUG_SCRIPT_DIR
export IDA_DEFAULT_INPUT_PATH

# 确保能导入 src/ 下模块
export PYTHONPATH="$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"

# 启动服务
CMD=(
    python3 -m ida_service.main
    --host "$IDA_SERVICE_HOST"
    --port "$IDA_SERVICE_PORT"
)

if [ -n "$IDA_DEFAULT_INPUT_PATH" ]; then
    CMD+=(--input-path "$IDA_DEFAULT_INPUT_PATH")
else
    echo "[INFO] IDA_DEFAULT_INPUT_PATH is empty; service will start without opening input."
    echo "[INFO] Use /db/open API to open binary or IDB later."
fi

"${CMD[@]}"
