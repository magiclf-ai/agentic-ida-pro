#!/usr/bin/env python3
"""Start observability backend API (Flask + SQLite)."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import threading
from pathlib import Path

from entrypoints.observability_api import serve_observability_api


def _build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent.parent.parent
    default_session_dir = root / "logs" / "agent_sessions"
    parser = argparse.ArgumentParser(description="Start observability API backend")
    parser.add_argument("--session-log-dir", default=str(default_session_dir), help="Session log directory")
    parser.add_argument(
        "--db-path",
        default="",
        help="SQLite db path (default: <session-log-dir>/agent_observability.sqlite3)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="API bind host")
    parser.add_argument("--port", type=int, default=8765, help="API bind port")
    parser.add_argument("--no-open-browser", action="store_true", help="Do not auto-open browser")
    return parser


def _browser_target_host(bind_host: str) -> str:
    host = str(bind_host or "").strip()
    if host in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return host or "127.0.0.1"


def _try_open_browser(url: str) -> bool:
    commands = []
    if shutil.which("wslview"):
        commands.append(["wslview", url])
    if shutil.which("xdg-open"):
        commands.append(["xdg-open", url])
    if shutil.which("gio"):
        commands.append(["gio", "open", url])
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if int(proc.returncode) == 0:
                return True
        except Exception:
            continue
    return False


def _schedule_browser_open(host: str, port: int) -> None:
    url = f"http://{_browser_target_host(host)}:{int(port)}/api/health"

    def _runner() -> None:
        if not _try_open_browser(url):
            print(f"[INFO] browser auto-open unavailable, open manually: {url}")

    threading.Timer(1.0, _runner).start()


def main() -> int:
    args = _build_parser().parse_args()
    db_path = str(args.db_path or "").strip()
    if not db_path:
        db_path = str(Path(args.session_log_dir).resolve() / "agent_observability.sqlite3")
    if not args.no_open_browser:
        _schedule_browser_open(host=args.host, port=int(args.port))
    print("[INFO] Frontend (separate project):")
    print("       cd frontend/observability")
    print("       npm install")
    print("       npm run dev -- --host 0.0.0.0 --port 5173")
    print(
        "       # optional when API is on another host: "
        "VITE_PROXY_TARGET='http://<flask-host>:{0}' npm run dev -- --host 0.0.0.0 --port 5173".format(
            int(args.port)
        )
    )
    return serve_observability_api(db_path=db_path, host=args.host, port=int(args.port))


if __name__ == "__main__":
    raise SystemExit(main())
