#!/usr/bin/env python3
"""Unified entrypoint: start ida_service subprocess, open binary/IDB, then run reverse agent."""
from __future__ import annotations

import asyncio
import argparse
import os
import re
import socket
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from clients import IDAClient
from entrypoints.reverse_expert import (
    DEFAULT_REPORT_DIR,
    run_from_namespace as run_reverse_expert_from_namespace,
)

LIKELY_BINARY_SUFFIXES = {
    ".i64",
    ".idb",
    ".i32",
    ".exe",
    ".dll",
    ".sys",
    ".drv",
    ".so",
    ".dylib",
    ".elf",
    ".bin",
    ".com",
    ".out",
    ".axf",
    ".ko",
    ".o",
    ".a",
}

IDA_SIDECAR_SUFFIXES = {
    ".id0",
    ".id1",
    ".id2",
    ".nam",
    ".til",
}

LIKELY_TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".xml",
    ".csv",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".vue",
    ".sh",
    ".bat",
    ".ps1",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
    ".rs",
    ".go",
    ".swift",
    ".kt",
    ".m",
}

DEFAULT_BATCH_PATTERNS = ["*"]


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
    parser.add_argument(
        "--input-path",
        default="",
        help="Input path. File=single-target mode; directory=batch mode",
    )
    parser.add_argument(
        "--input-dir",
        default="",
        help="Deprecated alias of --input-path (directory).",
    )
    parser.add_argument("--recursive", action="store_true", help="Recursively scan when input-path is a directory")
    parser.add_argument(
        "--file-pattern",
        action="append",
        default=[],
        help="Glob pattern for batch mode (repeatable, default='*')",
    )
    parser.add_argument("--concurrency", type=int, default=1, help="Batch worker count")

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
    parser.add_argument(
        "--agent-profile",
        choices=["struct_recovery", "attack_surface", "general_reverse"],
        default="struct_recovery",
        help="Reverse runtime profile",
    )
    parser.add_argument("--idapython-kb-dir", default="", help="Optional IDAPython KB dir")
    parser.add_argument("--report-dir", default="", help="Optional report directory")
    parser.add_argument("--case-id", default="", help="Optional eval case id")
    parser.add_argument("--case-spec-path", default="", help="Optional eval case spec markdown path")
    parser.add_argument("--evidence-function", action="append", default=[], help="Preferred function names for evidence decompile")
    return parser


def _port_probe_host(bind_host: str) -> str:
    host = str(bind_host or "").strip() or "127.0.0.1"
    if host in {"::", "[::]"}:
        return "127.0.0.1"
    return host


def _can_bind_port(bind_host: str, port: int) -> bool:
    host = _port_probe_host(bind_host)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, int(port)))
        return True
    except Exception:
        return False


def _allocate_dynamic_port(bind_host: str, base_port: int, reserved_ports: Optional[set[int]] = None) -> int:
    reserved = reserved_ports or set()
    start = max(1, int(base_port))

    for candidate in range(start, start + 2048):
        if candidate in reserved:
            continue
        if _can_bind_port(bind_host, candidate):
            return int(candidate)

    host = _port_probe_host(bind_host)
    for _ in range(128):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            candidate = int(sock.getsockname()[1])
        if candidate not in reserved:
            return candidate

    raise RuntimeError("failed to allocate available ida_service port")


def _is_probably_binary_file(path: Path) -> bool:
    try:
        data = path.read_bytes()[:4096]
    except Exception:
        return False
    if not data:
        return False
    if b"\x00" in data:
        return True
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    if text:
        control = 0
        for ch in text:
            code = ord(ch)
            if (code < 32 and ch not in {"\n", "\r", "\t", "\f", "\b"}) or code == 127:
                control += 1
        if control <= max(2, len(text) // 100):
            return False
    printable = set(range(32, 127)) | {7, 8, 9, 10, 12, 13, 27}
    non_printable = sum(1 for b in data if b not in printable)
    ratio = float(non_printable) / float(len(data))
    return ratio >= 0.30


def _is_supported_target_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in IDA_SIDECAR_SUFFIXES:
        return False
    if suffix in LIKELY_BINARY_SUFFIXES:
        return True
    if suffix in LIKELY_TEXT_SUFFIXES:
        return False
    return _is_probably_binary_file(path)


def _cleanup_ida_sidecar_files(root: Path, recursive: bool) -> Dict[str, Any]:
    base = Path(root).resolve()
    if not base.exists():
        return {"deleted_count": 0, "deleted_files": []}

    deleted: List[str] = []
    iterator = base.rglob("*") if bool(recursive) else base.glob("*")
    for item in iterator:
        if not item.is_file():
            continue
        if item.suffix.lower() not in IDA_SIDECAR_SUFFIXES:
            continue
        try:
            item.unlink()
            deleted.append(str(item))
        except Exception as e:
            print(f"[WARN] failed to delete sidecar: {item} ({e})")

    if deleted:
        print(
            f"[BATCH] deleted {len(deleted)} IDA sidecar files before target discovery "
            f"under {base}"
        )
    return {"deleted_count": len(deleted), "deleted_files": deleted}


def _iter_candidate_files(input_dir: Path, recursive: bool, patterns: Sequence[str]) -> List[Path]:
    root = input_dir.resolve()
    unique: Dict[str, Path] = {}
    for raw_pattern in patterns:
        pattern = str(raw_pattern or "").strip() or "*"
        iterator = input_dir.rglob(pattern) if recursive else input_dir.glob(pattern)
        for item in iterator:
            if not item.is_file():
                continue
            try:
                rel_parts = item.resolve().relative_to(root).parts
            except Exception:
                rel_parts = ()
            if any(str(part).lower() == "backups" for part in rel_parts):
                continue
            key = str(item.resolve())
            unique[key] = item.resolve()
    return [unique[key] for key in sorted(unique.keys())]


def _looks_like_generated_idb(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in {".i64", ".idb", ".i32"}:
        return False
    name = path.name
    return bool(re.search(r"_\d{14}(?:_[A-Za-z0-9._-]+)?\.(i64|idb|i32)$", name, flags=re.IGNORECASE))


def _organize_generated_idb_files(root: Path, recursive: bool) -> Dict[str, Any]:
    base = root.resolve()
    moved: List[str] = []
    iterator = base.rglob("*") if bool(recursive) else base.glob("*")
    for item in iterator:
        if not item.is_file():
            continue
        if not _looks_like_generated_idb(item):
            continue
        try:
            rel_parts = item.resolve().relative_to(base).parts
        except Exception:
            rel_parts = ()
        if any(str(part).lower() == "backups" for part in rel_parts):
            continue

        backup_dir = item.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / item.name
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            nonce = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = backup_dir / f"{stem}_moved_{nonce}{suffix}"
        try:
            item.rename(target)
            moved.append(f"{item} -> {target}")
        except Exception as e:
            print(f"[WARN] failed to move generated idb into backups: {item} ({e})")

    if moved:
        print(f"[BATCH] moved {len(moved)} generated idb files into backups")
    return {"moved_count": len(moved), "moved": moved}


def _discover_batch_targets(
    input_dir: str,
    recursive: bool,
    patterns: Sequence[str],
) -> List[str]:
    root = Path(str(input_dir or "").strip()).resolve()
    _organize_generated_idb_files(root, recursive=bool(recursive))
    _cleanup_ida_sidecar_files(root, recursive=bool(recursive))
    candidates = _iter_candidate_files(root, recursive=bool(recursive), patterns=patterns)
    by_key: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        if not _is_supported_target_file(item):
            continue
        suffix = item.suffix.lower()
        is_idb = suffix in {".i64", ".idb", ".i32"}
        key = item.stem if suffix else item.name
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = {"path": str(item), "is_idb": bool(is_idb)}
            continue
        if is_idb and (not bool(prev.get("is_idb", False))):
            by_key[key] = {"path": str(item), "is_idb": True}

    targets: List[str] = [str(row["path"]) for _k, row in sorted(by_key.items(), key=lambda kv: kv[0])]
    return targets


def _safe_tag(text: str) -> str:
    keep = []
    for ch in str(text or ""):
        if ch.isalnum() or ch in {"_", "-", "."}:
            keep.append(ch)
        else:
            keep.append("_")
    value = "".join(keep).strip("_")
    return value or "target"


def _build_reverse_run_namespace(
    *,
    args: argparse.Namespace,
    service_url: str,
    report_dir: str,
) -> argparse.Namespace:
    return argparse.Namespace(
        request=str(args.request),
        ida_url=service_url,
        max_iterations=int(args.max_iterations),
        agent_core=str(args.agent_core),
        agent_profile=str(args.agent_profile or "struct_recovery"),
        idapython_kb_dir=str(args.idapython_kb_dir or ""),
        report_dir=str(report_dir or DEFAULT_REPORT_DIR),
        case_id=str(getattr(args, "case_id", "") or ""),
        case_spec_path=str(getattr(args, "case_spec_path", "") or ""),
        evidence_function=list(getattr(args, "evidence_function", []) or []),
    )


def _run_single_target(args: argparse.Namespace, input_path: str, ida_port: Optional[int] = None, report_dir: str = "") -> int:
    input_path = os.path.abspath(str(input_path or "").strip())
    if not input_path or not os.path.exists(input_path):
        print(f"[ERROR] Input file not found: {input_path}")
        return 2

    bind_host = str(args.ida_host or "127.0.0.1").strip() or "127.0.0.1"
    resolved_port = int(ida_port) if ida_port is not None else _allocate_dynamic_port(bind_host, int(args.ida_port))
    service_url = str(args.ida_url or "").strip()
    if not service_url:
        service_url = f"http://{_service_access_host(bind_host)}:{resolved_port}"

    env = _build_env()
    service_cmd = [
        sys.executable,
        "-u",
        "-m",
        "ida_service.daemon",
        "--host",
        bind_host,
        "--port",
        str(resolved_port),
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

        reverse_args = _build_reverse_run_namespace(
            args=args,
            service_url=service_url,
            report_dir=(report_dir or str(args.report_dir or DEFAULT_REPORT_DIR)),
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


def _build_batch_child_cmd(
    *,
    args: argparse.Namespace,
    input_path: str,
    ida_port: int,
    report_dir: str,
) -> List[str]:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "reverse_agent.py"),
        "--request",
        str(args.request),
        "--input-path",
        str(input_path),
        "--ida-host",
        str(args.ida_host),
        "--ida-port",
        str(int(ida_port)),
        "--ida-log-dir",
        str(args.ida_log_dir),
        "--service-wait-timeout",
        str(int(args.service_wait_timeout)),
        "--max-iterations",
        str(int(args.max_iterations)),
        "--agent-core",
        str(args.agent_core),
        "--agent-profile",
        str(args.agent_profile or "struct_recovery"),
        "--report-dir",
        str(report_dir),
    ]
    if bool(args.ida_debug):
        cmd.append("--ida-debug")
    if bool(args.no_run_auto_analysis):
        cmd.append("--no-run-auto-analysis")
    if bool(args.no_save_on_exit):
        cmd.append("--no-save-on-exit")
    if str(args.idapython_kb_dir or "").strip():
        cmd.extend(["--idapython-kb-dir", str(args.idapython_kb_dir)])
    return cmd


async def _run_batch_worker(
    *,
    slot: int,
    args: argparse.Namespace,
    queue: "asyncio.Queue[Optional[Tuple[int, str]]]",
    results: List[Dict[str, Any]],
    batch_root: Path,
    port_lock: asyncio.Lock,
    reserved_ports: set[int],
) -> None:
    env = _build_env()

    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            return

        index, target_path = item
        start = time.time()
        target_name = Path(target_path).name
        report_dir = str(batch_root / f"{index:04d}_{_safe_tag(target_name)}")
        async with port_lock:
            worker_port = _allocate_dynamic_port(
                bind_host=str(args.ida_host or "127.0.0.1"),
                base_port=int(args.ida_port),
                reserved_ports=reserved_ports,
            )
            reserved_ports.add(int(worker_port))
        cmd = _build_batch_child_cmd(
            args=args,
            input_path=target_path,
            ida_port=worker_port,
            report_dir=report_dir,
        )
        print(f"[BATCH] slot={slot} port={worker_port} start {index}: {target_path}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(PROJECT_ROOT),
                env=env,
            )
            rc = await proc.wait()
        except Exception as e:
            rc = 1
            print(f"[BATCH][ERROR] slot={slot} target={target_path} error={e}")
        finally:
            async with port_lock:
                reserved_ports.discard(int(worker_port))
        elapsed = time.time() - start
        print(
            f"[BATCH] slot={slot} done {index}: rc={rc} elapsed={elapsed:.1f}s target={target_path}"
        )
        results.append(
            {
                "index": int(index),
                "target": str(target_path),
                "rc": int(rc),
                "elapsed_sec": float(elapsed),
                "worker_slot": int(slot),
                "worker_port": int(worker_port),
                "report_dir": report_dir,
            }
        )
        queue.task_done()


async def _run_batch_mode(args: argparse.Namespace, targets: Sequence[str]) -> int:
    concurrency = max(1, int(args.concurrency))
    queue: asyncio.Queue[Optional[Tuple[int, str]]] = asyncio.Queue()
    results: List[Dict[str, Any]] = []
    port_lock = asyncio.Lock()
    reserved_ports: set[int] = set()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_root = Path(str(args.report_dir or DEFAULT_REPORT_DIR)).resolve() / f"batch_{timestamp}"
    batch_root.mkdir(parents=True, exist_ok=True)

    for index, target in enumerate(targets, start=1):
        await queue.put((index, str(target)))
    for _ in range(concurrency):
        await queue.put(None)

    workers = [
        asyncio.create_task(
            _run_batch_worker(
                slot=slot,
                args=args,
                queue=queue,
                results=results,
                batch_root=batch_root,
                port_lock=port_lock,
                reserved_ports=reserved_ports,
            )
        )
        for slot in range(concurrency)
    ]

    await queue.join()
    await asyncio.gather(*workers)

    ordered = sorted(results, key=lambda row: int(row.get("index", 0)))
    success_count = sum(1 for row in ordered if int(row.get("rc", 1)) == 0)
    failed_count = len(ordered) - success_count
    print("\n" + "=" * 72)
    print(f"[BATCH] finished total={len(ordered)} success={success_count} failed={failed_count}")
    print(f"[BATCH] report root: {batch_root}")
    for row in ordered:
        print(
            "[BATCH][RESULT] "
            f"idx={int(row['index'])} rc={int(row['rc'])} "
            f"elapsed={float(row['elapsed_sec']):.1f}s "
            f"port={int(row['worker_port'])} "
            f"target={row['target']}"
        )
    return 0 if failed_count == 0 else 1


def _normalize_input_path(args: argparse.Namespace) -> Tuple[int, str, bool]:
    raw_input_path = str(args.input_path or "").strip()
    raw_input_dir = str(args.input_dir or "").strip()

    if raw_input_path and raw_input_dir:
        abs_path = os.path.abspath(raw_input_path)
        abs_dir = os.path.abspath(raw_input_dir)
        if abs_path != abs_dir:
            print("[ERROR] --input-path and --input-dir point to different locations.")
            return 2, "", False

    merged_raw = raw_input_path or raw_input_dir
    if not merged_raw:
        print("[ERROR] Missing --input-path.")
        return 2, "", False

    merged_path = os.path.abspath(merged_raw)
    if not os.path.exists(merged_path):
        print(f"[ERROR] Input path not found: {merged_path}")
        return 2, "", False

    if int(args.concurrency) <= 0:
        print("[ERROR] --concurrency must be > 0")
        return 2, "", False

    is_directory = bool(os.path.isdir(merged_path))
    if is_directory and str(args.ida_url or "").strip():
        print("[ERROR] Batch mode does not support --ida-url. Leave it empty.")
        return 2, "", False

    if raw_input_dir and (not raw_input_path):
        print("[WARN] --input-dir is deprecated. Use --input-path with a directory path.")

    return 0, merged_path, is_directory


def main() -> int:
    args = _build_parser().parse_args()
    validate_rc, input_path, is_directory = _normalize_input_path(args)
    if validate_rc != 0:
        return int(validate_rc)

    if not is_directory:
        single_suffix = Path(input_path).suffix.lower()
        if single_suffix in IDA_SIDECAR_SUFFIXES:
            print(
                "[ERROR] input-path points to IDA sidecar file "
                f"({single_suffix}). Use .i64/.idb/.i32 main database file instead: {input_path}"
            )
            return 2
        return _run_single_target(args=args, input_path=input_path, ida_port=int(args.ida_port), report_dir="")

    patterns = [str(item).strip() for item in (args.file_pattern or []) if str(item).strip()] or DEFAULT_BATCH_PATTERNS
    targets = _discover_batch_targets(
        input_dir=input_path,
        recursive=bool(args.recursive),
        patterns=patterns,
    )
    if not targets:
        print("[ERROR] No binary/IDB targets discovered in input directory.")
        return 3

    print(
        f"[BATCH] targets={len(targets)} concurrency={int(args.concurrency)} "
        f"port_strategy=dynamic(base={int(args.ida_port)}) recursive={bool(args.recursive)}"
    )
    for idx, path in enumerate(targets, start=1):
        print(f"[BATCH][TARGET] {idx}: {path}")

    try:
        return int(asyncio.run(_run_batch_mode(args, targets)))
    except KeyboardInterrupt:
        print("[WARN] batch interrupted by keyboard_interrupt")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
