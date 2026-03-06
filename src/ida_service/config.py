"""IDA Service 配置文件"""
import os


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# === 服务配置 ===
HOST = os.getenv("IDA_SERVICE_HOST", "127.0.0.1")
PORT = int(os.getenv("IDA_SERVICE_PORT", "5000"))

# === 调试配置 ===
# 调试模式：保留临时脚本文件，便于调试
DEBUG_MODE = os.getenv("IDA_DEBUG_MODE", "false").lower() == "true"

# 日志级别: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL = os.getenv("IDA_LOG_LEVEL", "DEBUG" if DEBUG_MODE else "INFO")

# 日志文件路径
LOG_DIR = os.getenv("IDA_LOG_DIR", os.path.join(PROJECT_ROOT, "logs"))
LOG_FILE = os.path.join(LOG_DIR, "ida_service.log")

# 临时脚本保留目录（调试用）
DEBUG_SCRIPT_DIR = os.getenv(
    "IDA_DEBUG_SCRIPT_DIR",
    os.path.join(PROJECT_ROOT, "logs", "scripts"),
)

# === 执行配置 ===
# 脚本执行超时（秒）
SCRIPT_TIMEOUT = int(os.getenv("IDA_SCRIPT_TIMEOUT", "60"))

# === IDA 配置 ===
# IDA 安装路径（可选，用于自动检测）
IDA_INSTALL_PATH = os.getenv("IDA_INSTALL_PATH", "")

# 启动时默认打开数据库
DEFAULT_IDB_PATH = os.getenv("IDA_DEFAULT_IDB_PATH", "")
