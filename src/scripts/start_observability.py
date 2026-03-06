#!/usr/bin/env python3
"""
统一启动可观测性系统（前端 + 后端）

用法:
    python src/scripts/start_observability.py [options]

示例:
    # 默认启动（后端 8765，前端 5173）
    python src/scripts/start_observability.py
    
    # 指定端口
    python src/scripts/start_observability.py --api-port 8080 --ui-port 3000
    
    # 只启动后端
    python src/scripts/start_observability.py --backend-only
    
    # 只启动前端
    python src/scripts/start_observability.py --frontend-only
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
FRONTEND_DIR = PROJECT_ROOT / "frontend" / "observability"
DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 8765
DEFAULT_UI_PORT = 5173


class ProcessManager:
    """管理前后端进程"""
    
    def __init__(self):
        self.processes = []
        self._shutting_down = False
    
    def add(self, proc: subprocess.Popen, name: str):
        self.processes.append((proc, name))
    
    def shutdown(self, signum=None, frame=None):
        if self._shutting_down:
            return
        self._shutting_down = True
        
        print("\n\n[Observability] 正在停止服务...")
        for proc, name in self.processes:
            if proc.poll() is None:
                print(f"  停止 {name} (PID: {proc.pid})")
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        
        print("[Observability] 所有服务已停止")
        sys.exit(0)


def check_node_modules() -> bool:
    """检查前端依赖是否已安装"""
    return (FRONTEND_DIR / "node_modules").exists()


def install_frontend_deps():
    """安装前端依赖"""
    print(f"[Frontend] 安装依赖中...")
    result = subprocess.run(
        ["npm", "install"],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[Frontend] 依赖安装失败: {result.stderr}")
        return False
    print(f"[Frontend] 依赖安装完成")
    return True


def start_backend(host: str, port: int, db_path: str = None, reload: bool = True) -> subprocess.Popen:
    """启动后端 API 服务（支持热重载）"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    # 启用 Flask 热重载
    if reload:
        env["FLASK_ENV"] = "development"
        env["FLASK_DEBUG"] = "1"
    
    cmd = [
        sys.executable,
        "-u",  # unbuffered 输出，确保日志实时显示
        str(PROJECT_ROOT / "src" / "scripts" / "logs.py"),
        "--host", host,
        "--port", str(port),
        "--no-open-browser",
    ]
    
    if db_path:
        cmd.extend(["--db-path", db_path])
    
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    
    return proc


def start_frontend(host: str, port: int, api_host: str, api_port: int, reload: bool = True) -> subprocess.Popen:
    """启动前端开发服务器（Vite 自带热更新）"""
    env = os.environ.copy()
    # 设置代理目标为后端 API
    # 如果 api_host 是 0.0.0.0，代理需要用 127.0.0.1
    proxy_host = "127.0.0.1" if api_host == "0.0.0.0" else api_host
    env["VITE_PROXY_TARGET"] = f"http://{proxy_host}:{api_port}"
    
    # Vite dev server 默认支持热更新 (HMR)
    cmd = [
        "npm", "run", "dev",
        "--", "--host", host, "--port", str(port)
    ]
    
    proc = subprocess.Popen(
        cmd,
        cwd=FRONTEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    
    return proc


def stream_output(proc: subprocess.Popen, prefix: str, color: str):
    """流式输出进程日志"""
    colors = {
        "blue": "\033[36m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "reset": "\033[0m",
    }
    color_code = colors.get(color, "")
    reset_code = colors["reset"]
    
    for line in proc.stdout:
        print(f"{color_code}[{prefix}]{reset_code} {line}", end="")


def wait_for_service(port: int, timeout: int = 30, host: str = "127.0.0.1", name: str = "service") -> bool:
    """等待服务启动"""
    import socket
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    
    return False


def print_banner(api_host: str, api_port: int, ui_port: int):
    """打印启动横幅"""
    print("\n" + "=" * 60)
    print("  🚀 Agentic IDA 可观测性系统已启动")
    print("=" * 60)
    
    # 判断是否是本地绑定
    is_local = api_host in ("127.0.0.1", "localhost")
    
    if is_local:
        print(f"\n  📊 前端界面: http://127.0.0.1:{ui_port}")
        print(f"  🔌 后端 API: http://127.0.0.1:{api_port}")
    else:
        print(f"\n  📊 前端界面: http://<本机IP>:{ui_port}")
        print(f"  🔌 后端 API: http://{api_host}:{api_port}")
        print(f"\n  ⚠️  注意: API 绑定在 {api_host}，确保前端能通过该地址访问")
    
    print("\n  按 Ctrl+C 停止所有服务")
    print("=" * 60 + "\n")


def get_default_api_host():
    """获取默认 API 主机地址"""
    import socket
    try:
        # 获取本机 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    default_host = get_default_api_host()
    
    parser = argparse.ArgumentParser(
        description="启动可观测性系统（前端 + 后端，默认启用热更新）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
示例:
  %(prog)s                           # 默认启动（热更新启用）
  %(prog)s --no-reload               # 禁用热更新
  %(prog)s --api-host 192.168.1.100  # 指定后端 IP（跨机器部署）
  %(prog)s --api-port 8080           # 指定后端端口
  %(prog)s --ui-port 3000            # 指定前端端口
  %(prog)s --backend-only            # 只启动后端
  %(prog)s --frontend-only           # 只启动前端

热更新说明:
  - 后端: Flask debug 模式，修改 Python 文件自动重载
  - 前端: Vite HMR，修改 Vue/CSS 文件即时生效

默认配置:
  后端 API: 0.0.0.0:{DEFAULT_API_PORT} (监听所有接口)
  前端 UI: 0.0.0.0:{DEFAULT_UI_PORT} (监听所有接口)
  本机 IP: {default_host}
        """
    )
    parser.add_argument("--api-host", type=str, default=DEFAULT_API_HOST,
                        help=f"后端 API 绑定地址 (默认: {DEFAULT_API_HOST}, 建议跨机器时指定本机IP)")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT,
                        help=f"后端 API 端口 (默认: {DEFAULT_API_PORT})")
    parser.add_argument("--ui-host", type=str, default="0.0.0.0",
                        help="前端 UI 绑定地址 (默认: 0.0.0.0)")
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT,
                        help=f"前端 UI 端口 (默认: {DEFAULT_UI_PORT})")
    parser.add_argument("--db-path", type=str, default=None,
                        help="自定义数据库路径")
    parser.add_argument("--backend-only", action="store_true",
                        help="只启动后端 API")
    parser.add_argument("--frontend-only", action="store_true",
                        help="只启动前端 UI")
    parser.add_argument("--skip-deps-check", action="store_true",
                        help="跳过前端依赖检查")
    parser.add_argument("--no-reload", action="store_true",
                        help="禁用热重载（默认启用）")

    args = parser.parse_args()
    
    manager = ProcessManager()
    signal.signal(signal.SIGINT, manager.shutdown)
    signal.signal(signal.SIGTERM, manager.shutdown)
    
    # 检查虚拟环境
    if not str(sys.executable).endswith(".venv/bin/python"):
        venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
        if venv_python.exists():
            print(f"[Info] 建议使用虚拟环境: {venv_python}")
            print(f"[Info] 当前: {sys.executable}\n")
    
    # 确定用于访问的 API 地址（展示给用户）
    api_access_host = args.api_host if args.api_host not in ("0.0.0.0", "127.0.0.1") else get_default_api_host()
    
    reload = not args.no_reload
    reload_status = "热更新模式" if reload else "标准模式"

    try:
        # 启动后端
        if not args.frontend_only:
            print(f"[Backend] 启动 API 服务 ({args.api_host}:{args.api_port}) [{reload_status}]...")
            backend_proc = start_backend(args.api_host, args.api_port, args.db_path, reload=reload)
            manager.add(backend_proc, "Backend")

            # 等待后端启动（如果是 0.0.0.0，用 127.0.0.1 检查）
            check_host = "127.0.0.1" if args.api_host == "0.0.0.0" else args.api_host
            if not wait_for_service(args.api_port, timeout=30, host=check_host, name="Backend"):
                print("[Backend] 启动超时，请检查日志")
                manager.shutdown()
                return 1
            print(f"[Backend] 服务已启动 ✅\n")

        # 启动前端
        if not args.backend_only:
            # 检查依赖
            if not args.skip_deps_check and not check_node_modules():
                if not install_frontend_deps():
                    print("[Frontend] 依赖安装失败，请手动运行: npm install")
                    return 1

            print(f"[Frontend] 启动 UI 服务 ({args.ui_host}:{args.ui_port}) [{reload_status}]...")
            frontend_proc = start_frontend(args.ui_host, args.ui_port, args.api_host, args.api_port, reload=reload)
            manager.add(frontend_proc, "Frontend")

            # 等待前端启动
            check_host = "127.0.0.1" if args.ui_host == "0.0.0.0" else args.ui_host
            if not wait_for_service(args.ui_port, timeout=60, host=check_host, name="Frontend"):
                print("[Frontend] 启动超时，请检查日志")
                manager.shutdown()
                return 1
            print(f"[Frontend] 服务已启动 ✅\n")
        
        # 打印横幅
        if args.backend_only:
            print(f"\n[Backend] 单独运行模式")
            print(f"  API: http://{api_access_host}:{args.api_port}")
        elif args.frontend_only:
            print(f"\n[Frontend] 单独运行模式")
            print(f"  UI: http://{args.ui_host}:{args.ui_port}")
        else:
            print_banner(api_access_host, args.api_port, args.ui_port)
        
        # 流式输出日志
        import threading
        threads = []
        
        for proc, name in manager.processes:
            color = "green" if name == "Backend" else "blue"
            t = threading.Thread(
                target=stream_output,
                args=(proc, name, color),
                daemon=True,
            )
            t.start()
            threads.append(t)
        
        # 等待所有进程结束
        for proc, name in manager.processes:
            proc.wait()
        
    except KeyboardInterrupt:
        manager.shutdown()
    except Exception as e:
        print(f"\n[Error] {e}")
        manager.shutdown()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
