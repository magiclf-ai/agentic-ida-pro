#!/usr/bin/env python3
"""开发协作用服务桥接：WSL 请求重启，Windows 监听并重启 ida service。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONTROL_FILE = os.getenv(
    "IDA_SERVICE_CONTROL_FILE",
    os.path.join(PROJECT_ROOT, "runtime", "ida_service_control.json"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent(path: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def read_state(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"version": 1, "request": None, "ack": None}
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    if "version" not in state:
        state["version"] = 1
    if "request" not in state:
        state["request"] = None
    if "ack" not in state:
        state["ack"] = None
    return state


def write_state(path: str, state: Dict[str, Any]):
    ensure_parent(path)
    target_dir = os.path.dirname(path) or "."
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=target_dir) as tf:
        json.dump(state, tf, ensure_ascii=False, indent=2)
        tf.write("\n")
        temp_path = tf.name
    os.replace(temp_path, path)


def request_restart(path: str, reason: str, requester: str) -> str:
    state = read_state(path)
    request_id = str(uuid.uuid4())
    state["request"] = {
        "id": request_id,
        "action": "restart",
        "reason": reason,
        "requester": requester,
        "requested_at": now_iso(),
    }
    write_state(path, state)
    return request_id


def wait_ack(path: str, request_id: str, timeout_sec: int, poll_sec: float = 1.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        state = read_state(path)
        ack = state.get("ack")
        if isinstance(ack, dict) and ack.get("id") == request_id:
            return ack
        time.sleep(poll_sec)
    raise TimeoutError(f"Timed out waiting ack for request_id={request_id}")


def start_service(command: str, cwd: Optional[str]) -> subprocess.Popen:
    proc = subprocess.Popen(command, shell=True, cwd=cwd)
    print(f"[bridge] service started pid={proc.pid}")
    return proc


def stop_service(proc: subprocess.Popen, timeout_sec: int = 20):
    if proc.poll() is not None:
        return
    print(f"[bridge] stopping pid={proc.pid}")
    try:
        proc.terminate()
        proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        print(f"[bridge] terminate timeout, kill pid={proc.pid}")
        proc.kill()
        proc.wait(timeout=5)


def cmd_request(args):
    req_id = request_restart(args.control_file, reason=args.reason, requester=args.requester)
    print(f"[OK] restart request sent: {req_id}")
    print(f"control_file={args.control_file}")
    if args.wait:
        ack = wait_ack(args.control_file, request_id=req_id, timeout_sec=args.timeout)
        print(f"[OK] ack received: {ack}")


def cmd_watch(args):
    ensure_parent(args.control_file)
    print(f"[bridge] watching control file: {args.control_file}")
    proc = start_service(args.service_cmd, cwd=args.cwd)
    last_handled: Optional[str] = None

    try:
        last_heartbeat = 0.0
        while True:
            if proc.poll() is not None:
                print(f"[bridge] service exited rc={proc.returncode}, respawning...")
                time.sleep(args.respawn_delay)
                proc = start_service(args.service_cmd, cwd=args.cwd)

            state = read_state(args.control_file)
            req = state.get("request")
            if isinstance(req, dict):
                req_id = req.get("id")
                action = req.get("action")
                if action == "restart" and req_id and req_id != last_handled:
                    print(f"[bridge] restart requested id={req_id}, reason={req.get('reason', '')}")
                    stop_service(proc)
                    proc = start_service(args.service_cmd, cwd=args.cwd)

                    state = read_state(args.control_file)
                    state["ack"] = {
                        "id": req_id,
                        "status": "restarted",
                        "pid": proc.pid,
                        "acked_at": now_iso(),
                    }
                    write_state(args.control_file, state)
                    print(f"[bridge] ack written for id={req_id}")
                    last_handled = req_id

            now = time.time()
            if now - last_heartbeat >= 30:
                print(f"[bridge] alive pid={proc.pid}")
                last_heartbeat = now

            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("[bridge] interrupted, shutting down...")
        stop_service(proc)


def main():
    parser = argparse.ArgumentParser(description="IDA service restart bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    req = sub.add_parser("request", help="Request restart from WSL")
    req.add_argument("--control-file", default=DEFAULT_CONTROL_FILE, help="Control file path")
    req.add_argument("--reason", default="ida_service_code_changed", help="Restart reason")
    req.add_argument("--requester", default="wsl", help="Requester id")
    req.add_argument("--wait", action="store_true", help="Wait for restart ack")
    req.add_argument("--timeout", type=int, default=180, help="Ack wait timeout seconds")
    req.set_defaults(func=cmd_request)

    watch = sub.add_parser("watch", help="Watch request file and restart service (Windows)")
    watch.add_argument("--service-cmd", required=True, help="ida service start command")
    watch.add_argument("--control-file", default=DEFAULT_CONTROL_FILE, help="Control file path")
    watch.add_argument("--cwd", default=None, help="Working directory")
    watch.add_argument("--poll-interval", type=float, default=1.0, help="Polling interval seconds")
    watch.add_argument("--respawn-delay", type=float, default=2.0, help="Respawn delay seconds")
    watch.set_defaults(func=cmd_watch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
