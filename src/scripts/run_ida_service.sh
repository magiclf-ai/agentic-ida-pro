#!/bin/bash
# IDA Service 启动脚本

# 设置默认配置
IDA_SERVICE_HOST="${IDA_SERVICE_HOST:-127.0.0.1}"
IDA_SERVICE_PORT="${IDA_SERVICE_PORT:-5000}"
IDA_DEBUG_MODE="${IDA_DEBUG_MODE:-false}"
IDA_LOG_LEVEL="${IDA_LOG_LEVEL:-INFO}"
IDA_LOG_DIR="${IDA_LOG_DIR:-$(pwd)/logs}"
IDA_DEBUG_SCRIPT_DIR="${IDA_DEBUG_SCRIPT_DIR:-$(pwd)/logs/scripts}"
IDA_DEFAULT_IDB_PATH="${IDA_DEFAULT_IDB_PATH:-}"

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
if [ -n "$IDA_DEFAULT_IDB_PATH" ]; then
    echo "Default IDB: $IDA_DEFAULT_IDB_PATH"
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
export IDA_DEFAULT_IDB_PATH

# 需要在服务启动时打开数据库
if [ -z "$IDA_DEFAULT_IDB_PATH" ]; then
    echo "[ERROR] IDA_DEFAULT_IDB_PATH is empty. Please provide an IDB/I64 path."
    exit 1
fi

# 确保能导入 src/ 下模块
export PYTHONPATH="$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"

# 启动服务
python3 -m ida_service.main \
    --host "$IDA_SERVICE_HOST" \
    --port "$IDA_SERVICE_PORT" \
    --idb "$IDA_DEFAULT_IDB_PATH"
