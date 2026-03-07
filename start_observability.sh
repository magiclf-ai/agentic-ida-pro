#!/bin/bash
# 一键启动可观测性系统
# 用法: ./start_observability.sh [options]

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT_ROOT" || exit 1

# 使用虚拟环境的 Python
PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "❌ 虚拟环境未找到，请先创建:"
    echo "   python3 -m venv .venv"
    echo "   .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# 执行启动脚本
exec "$PYTHON" "$PROJECT_ROOT/src/entrypoints/observability_stack.py" "$@"
