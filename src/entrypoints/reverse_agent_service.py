#!/usr/bin/env python3
"""Unified entrypoint: start ida_service subprocess, open binary/IDB, then run reverse agent."""
from __future__ import annotations

import asyncio
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent.ida_client import IDAClient
from entrypoints.reverse_expert import (
    DEFAULT_REPORT_DIR,
    run_from_namespace as run_reverse_expert_from_namespace,
)


def _service_access_host(bind_host: str) -> str:
    value = str(bind_host or "").strip()
    if value in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return value or "127.0.0.1"


def _build_env() -> Dict[str, str]:
    env = os.environ.copy()
    src_path = str(SRC_ROOT)
    existing = str(env.get("PYTHONPATH", "")).strip()
    if existing:
        if src_path not in existing.split(os.pathsep):
            env["PYTHONPATH"] = src_path + os.pathsep + existing
    else:
        env["PYTHONPATH"] = src_path
    return env


def _terminate_process(proc: Optional[subprocess.Popen], name: str, timeout_sec: int = 15) -> None:
    if proc is None or proc.poll() is not None:
        return
    print(f"[INFO] stopping {name} pid={proc.pid}")
    try:
        proc.terminate()
        proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        print(f"[WARN] terminate timeout for {name}, killing pid={proc.pid}")
        proc.kill()
        proc.wait(timeout=5)


def _wait_service_ready(
    client: IDAClient,
    service_proc: subprocess.Popen,
    timeout_sec: int,
) -> None:
    deadline = time.time() + max(1, int(timeout_sec))
    last_error = ""
    while time.time() < deadline:
        if service_proc.poll() is not None:
            raise RuntimeError(f"ida_service exited early rc={service_proc.returncode}")
        try:
            health = client.health_check()
            if str(health.get("status", "")) == "ok":
                print(f"[INFO] ida_service healthy: {health}")
                return
        except requests.RequestException as e:
            last_error = str(e)
        except Exception as e:
            last_error = str(e)
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting ida_service health. last_error={last_error}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start ida_service subprocess, open binary/IDB, then run reverse expert agent",
    )
    parser.add_argument("--request", required=True, help="Reverse-analysis request")
    parser.add_argument("--input-path", required=True, help="Binary or IDB path to open before agent run")

    parser.add_argument("--ida-host", default="127.0.0.1", help="ida_service bind host")
    parser.add_argument("--ida-port", type=int, default=5000, help="ida_service port")
    parser.add_argument("--ida-url", default="", help="ida_service URL for client (optional)")
    parser.add_argument("--ida-log-dir", default=str(PROJECT_ROOT / "logs"), help="ida_service log directory")
    parser.add_argument("--ida-debug", action="store_true", help="Enable ida_service debug mode")
    parser.add_argument("--service-wait-timeout", type=int, default=90, help="ida_service health wait timeout (seconds)")
    parser.add_argument("--no-run-auto-analysis", action="store_true", help="Disable auto-analysis when opening database")
    parser.add_argument("--no-save-on-exit", action="store_true", help="Do not save database on close_database")

    parser.add_argument("--max-iterations", type=int, default=24, help="Agent max iterations")
    parser.add_argument(
        "--agent-core",
        choices=["struct_recovery", "dispatcher"],
        default="struct_recovery",
        help="Agent core entrypoint",
    )
    parser.add_argument("--idapython-kb-dir", default="", help="Optional IDAPython KB dir")
    parser.add_argument("--report-dir", default="", help="Optional report directory")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    input_path = os.path.abspath(str(args.input_path or "").strip())
    if not input_path or not os.path.exists(input_path):
        print(f"[ERROR] Input file not found: {input_path}")
        return 2

    bind_host = str(args.ida_host or "127.0.0.1").strip() or "127.0.0.1"
    service_url = str(args.ida_url or "").strip()
    if not service_url:
        service_url = f"http://{_service_access_host(bind_host)}:{int(args.ida_port)}"

    env = _build_env()
    service_cmd = [
        sys.executable,
        "-u",
        "-m",
        "ida_service.daemon",
        "--host",
        bind_host,
        "--port",
        str(int(args.ida_port)),
        "--log-dir",
        str(args.ida_log_dir),
    ]
    if bool(args.ida_debug):
        service_cmd.append("--debug")

    service_proc: Optional[subprocess.Popen] = None
    client = IDAClient(base_url=service_url)
    service_ready = False
    exit_code = 0
    interrupted_by = ""

    previous_handlers: Dict[int, Any] = {}

    def _on_signal(signum, frame):
        nonlocal interrupted_by
        try:
            interrupted_by = signal.Signals(signum).name
        except Exception:
            interrupted_by = f"SIGNAL_{int(signum)}"
        raise KeyboardInterrupt(interrupted_by)

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        previous_handlers[int(sig)] = signal.getsignal(sig)
        signal.signal(sig, _on_signal)

    try:
        print(f"[INFO] starting ida_service: {' '.join(service_cmd)}")
        service_proc = subprocess.Popen(
            service_cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
        )

        _wait_service_ready(
            client=client,
            service_proc=service_proc,
            timeout_sec=int(args.service_wait_timeout),
        )
        service_ready = True

        open_result = client.open_database(
            input_path=input_path,
            run_auto_analysis=(not bool(args.no_run_auto_analysis)),
            save_current=True,
        )
        print(f"[INFO] database opened: {open_result}")

        reverse_args = argparse.Namespace(
            request=str(args.request),
            ida_url=service_url,
            max_iterations=int(args.max_iterations),
            agent_core=str(args.agent_core),
            idapython_kb_dir=str(args.idapython_kb_dir or ""),
            report_dir=str(args.report_dir or DEFAULT_REPORT_DIR),
        )
        print("[INFO] starting reverse agent in current process")
        exit_code = int(asyncio.run(run_reverse_expert_from_namespace(reverse_args)))

    except KeyboardInterrupt:
        print(f"[WARN] interrupted by {interrupted_by or 'keyboard_interrupt'}")
        exit_code = 130
    except Exception as e:
        print(f"[ERROR] failed: {e}")
        exit_code = 1
    finally:
        if service_ready:
            try:
                close_result = client.close_database(save=(not bool(args.no_save_on_exit)))
                print(f"[INFO] database closed: {close_result}")
            except Exception as e:
                print(f"[WARN] close_database failed: {e}")

        _terminate_process(service_proc, "ida_service")

        for raw_sig, handler in previous_handlers.items():
            try:
                signal.signal(raw_sig, handler)
            except Exception:
                pass

    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
