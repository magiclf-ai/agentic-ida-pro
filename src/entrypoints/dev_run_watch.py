#!/usr/bin/env python3
"""Non-blocking dev watch entrypoint for managed reverse-agent runs."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import sqlite3
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from entrypoints.observability_api import _connect, _fetch_events, _fetch_session_summary, _fetch_sessions


DEFAULT_OBS_DB_PATH = PROJECT_ROOT / "logs" / "agent_sessions" / "agent_observability.sqlite3"
DEFAULT_RUN_ROOT = PROJECT_ROOT / "logs" / "dev_runs"
DEFAULT_REQUESTS = {
    "struct_recovery": "请分析目标程序并恢复关键结构体，输出恢复结果与改动说明。",
    "attack_surface": "请分析目标程序的攻击面、输入入口与危险调用链，输出可执行风险摘要。",
    "general_reverse": "请分析目标程序的关键逻辑、调用关系与数据流，输出逆向摘要。",
}
LOCAL_HTTP = requests.Session()
LOCAL_HTTP.trust_env = False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _meta_path(run_dir: Path) -> Path:
    return run_dir / "meta.json"


def _watch_log_path(run_dir: Path) -> Path:
    return run_dir / "watch.log"


def _stdout_log_path(run_dir: Path) -> Path:
    return run_dir / "stdout.log"


def _stderr_log_path(run_dir: Path) -> Path:
    return run_dir / "stderr.log"


def _exit_code_path(run_dir: Path) -> Path:
    return run_dir / "exit_code.txt"


def _load_meta(run_id: str, run_root: Path) -> Dict[str, Any]:
    run_dir = run_root / str(run_id or "").strip()
    meta_file = _meta_path(run_dir)
    if not meta_file.exists():
        raise FileNotFoundError(f"run not found: {run_id}")
    return json.loads(meta_file.read_text(encoding="utf-8"))


def _save_meta(meta: Dict[str, Any]) -> None:
    run_dir = Path(str(meta["run_dir"]))
    run_dir.mkdir(parents=True, exist_ok=True)
    _meta_path(run_dir).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _service_access_host(bind_host: str) -> str:
    value = str(bind_host or "").strip()
    if value in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return value or "127.0.0.1"


def _can_bind_port(bind_host: str, port: int) -> bool:
    host = _service_access_host(bind_host)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, int(port)))
        except Exception:
            return False
    return True


def _allocate_port(bind_host: str, preferred_port: int) -> int:
    start = max(1, int(preferred_port or 5000))
    for candidate in range(start, start + 512):
        if _can_bind_port(bind_host, candidate):
            return int(candidate)
    host = _service_access_host(bind_host)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _default_request(profile: str) -> str:
    selected = str(profile or "struct_recovery").strip() or "struct_recovery"
    return DEFAULT_REQUESTS.get(selected, DEFAULT_REQUESTS["struct_recovery"])


def _shell_join(args: List[str]) -> str:
    return " ".join(shlex.quote(str(item)) for item in args)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8")


def _append_watch_block(run_dir: Path, block_type: str, payload: Dict[str, Any]) -> None:
    block = _render_text_block(block_type, payload)
    with _watch_log_path(run_dir).open("a", encoding="utf-8") as f:
        f.write(block)
        if not block.endswith("\n"):
            f.write("\n")
        f.write("\n")


def _render_text_block(block_type: str, payload: Dict[str, Any]) -> str:
    lines = [str(block_type or "BLOCK").strip()]
    for key, value in payload.items():
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
        elif isinstance(value, dict):
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            rendered = str(value)
        lines.append(f"- {key}: {rendered}")
    return "\n".join(lines)


def _emit_stdout(block_type: str, payload: Dict[str, Any], output_format: str) -> None:
    if str(output_format or "text") == "jsonl":
        print(json.dumps({"type": block_type, **payload}, ensure_ascii=False))
        return
    print(_render_text_block(block_type, payload))


def _process_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_exit_code(run_dir: Path) -> Optional[int]:
    path = _exit_code_path(run_dir)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _update_meta(run_dir: Path, updates: Dict[str, Any]) -> Dict[str, Any]:
    meta = json.loads(_meta_path(run_dir).read_text(encoding="utf-8"))
    meta.update(updates)
    _save_meta(meta)
    return meta


def _signal_group(pid: int, signum: int) -> None:
    os.killpg(int(pid), int(signum))


def _extract_session_id_from_stdout(stdout_path: Path) -> str:
    if not stdout_path.exists():
        return ""
    try:
        text = stdout_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    match = re.search(r"\[LOG\]\s+Session ID:\s+([0-9_]+_[0-9a-fA-F]+)", text)
    return str(match.group(1)) if match else ""


def _infer_session_id_from_db(meta: Dict[str, Any]) -> str:
    db_path = Path(str(meta.get("observability_db") or DEFAULT_OBS_DB_PATH))
    if not db_path.exists():
        return ""
    run_id = str(meta.get("run_id") or "").strip()
    try:
        with _connect(str(db_path)) as conn:
            if run_id:
                rows = conn.execute(
                    """
                    SELECT session_id, payload_text
                    FROM session_events
                    WHERE event='session_started'
                    ORDER BY id DESC
                    LIMIT 200
                    """
                ).fetchall()
                for row in rows:
                    payload_text = str(row["payload_text"] or "").strip()
                    if not payload_text:
                        continue
                    try:
                        payload = json.loads(payload_text)
                    except Exception:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if str(payload.get("watch_run_id", "") or "").strip() != run_id:
                        continue
                    return str(row["session_id"] or "")
                return ""
            sessions = _fetch_sessions(conn)
    except Exception:
        return ""
    started_at = str(meta.get("started_at") or "")
    target_name = Path(str(meta.get("target") or "")).name
    candidates: List[Dict[str, Any]] = []
    for row in sessions:
        created_at = str(row.get("created_at") or "")
        if started_at and created_at and created_at < started_at:
            continue
        binary_name = str(row.get("binary_name") or "")
        if target_name and binary_name and Path(binary_name).name != target_name:
            continue
        candidates.append(row)
    if not candidates and sessions:
        candidates = sessions[:3]
    if not candidates:
        return ""
    candidates.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return str(candidates[0].get("session_id") or "")


def _resolve_session_id(meta: Dict[str, Any], run_root: Path) -> str:
    session_id = str(meta.get("session_id") or "")
    if session_id:
        return session_id
    session_id = _extract_session_id_from_stdout(_stdout_log_path(Path(str(meta["run_dir"]))))
    if not session_id:
        session_id = _infer_session_id_from_db(meta)
    if session_id:
        meta["session_id"] = session_id
        _save_meta(meta)
    return session_id


def _fetch_summary(meta: Dict[str, Any], run_root: Path) -> Dict[str, Any]:
    session_id = _resolve_session_id(meta, run_root)
    if not session_id:
        return {}
    db_path = Path(str(meta.get("observability_db") or DEFAULT_OBS_DB_PATH))
    if not db_path.exists():
        return {}
    try:
        with _connect(str(db_path)) as conn:
            return _fetch_session_summary(conn, session_id)
    except Exception:
        return {}


def _append_session_event(meta: Dict[str, Any], run_root: Path, event: str, payload: Dict[str, Any]) -> None:
    session_id = _resolve_session_id(meta, run_root)
    if not session_id:
        return
    db_path = Path(str(meta.get("observability_db") or DEFAULT_OBS_DB_PATH))
    if not db_path.exists():
        return
    now = _utc_now_iso()
    try:
        with sqlite3.connect(str(db_path), timeout=10) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM session_events WHERE session_id=?",
                (session_id,),
            ).fetchone()
            next_seq = int((row[0] if row else 0) or 0) + 1
            conn.execute(
                """
                INSERT INTO session_events(session_id, seq, event, payload_text, created_at)
                VALUES(?,?,?,?,?)
                """,
                (
                    session_id,
                    next_seq,
                    str(event or "").strip(),
                    json.dumps(payload or {}, ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE session_id=?",
                (now, session_id),
            )
            conn.commit()
    except Exception:
        return


def _fetch_event_tail(meta: Dict[str, Any], run_root: Path, limit: int) -> List[Dict[str, Any]]:
    session_id = _resolve_session_id(meta, run_root)
    if not session_id:
        return []
    db_path = Path(str(meta.get("observability_db") or DEFAULT_OBS_DB_PATH))
    if not db_path.exists():
        return []
    try:
        with _connect(str(db_path)) as conn:
            return _fetch_events(conn, session_id, limit=limit, after_seq=0)
    except Exception:
        return []


def _query_ida_status(meta: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(meta.get("ida_url") or "").strip()
    if not base_url:
        return {"service_alive": False}
    try:
        resp = LOCAL_HTTP.get(f"{base_url}/status", timeout=5)
        data = resp.json() if resp.ok else {}
        service = data.get("service", {}) if isinstance(data, dict) else {}
        return {
            "service_alive": bool(resp.ok),
            "last_script": str(service.get("last_script", "") or ""),
            "timeout_count": int(service.get("timeout_count", 0) or 0),
            "db_path": str(service.get("db_path", "") or ""),
            "is_executing": bool(service.get("is_executing", False)),
            "last_event": str(service.get("last_event", "") or ""),
            "last_event_at": str(service.get("last_event_at", "") or ""),
            "last_duration_ms": int(service.get("last_duration_ms", 0) or 0),
            "last_error": str(service.get("last_error", "") or ""),
        }
    except Exception as e:
        return {
            "service_alive": False,
            "error": str(e),
            "db_path": "",
            "timeout_count": 0,
            "last_script": "",
        }


def _elapsed_seconds(started_at: str) -> int:
    value = str(started_at or "").strip()
    if not value:
        return 0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))


def _build_status_payload(meta: Dict[str, Any], run_root: Path) -> Dict[str, Any]:
    run_dir = Path(str(meta["run_dir"]))
    pid = int(meta.get("pid", 0) or 0)
    alive = _process_alive(pid) if pid > 0 else False
    exit_code = _read_exit_code(run_dir)
    summary = _fetch_summary(meta, run_root)
    ida_status = _query_ida_status(meta)
    latest_turn = summary.get("latest_turn", {}) if isinstance(summary, dict) else {}
    latest_tool_batch = summary.get("latest_tool_batch", {}) if isinstance(summary, dict) else {}
    mutation_status = summary.get("mutation_status", {}) if isinstance(summary, dict) else {}
    stop_reason = str(summary.get("stop_reason", "") or "")
    latest_progress_at = str(summary.get("latest_progress_at", "") or meta.get("started_at", ""))
    status = str(summary.get("status", "") or ("running" if alive else "finished"))
    stop_requested_reason = str(meta.get("stop_requested_reason", "") or "")

    if not alive and status in {"", "running"}:
        if stop_requested_reason:
            status = "stopped"
            stop_reason = stop_requested_reason
        elif exit_code is None:
            status = "finished"
        elif int(exit_code) == 0:
            status = "finished"
        else:
            status = "failed"
    if not stop_reason and stop_requested_reason and not alive:
        stop_reason = stop_requested_reason

    return {
        "run_id": str(meta["run_id"]),
        "run_dir": str(run_dir),
        "target": str(meta.get("target") or ""),
        "request": str(meta.get("request") or ""),
        "agent_profile": str(meta.get("agent_profile") or ""),
        "session_id": str(meta.get("session_id") or ""),
        "started_at": str(meta.get("started_at") or ""),
        "elapsed_sec": _elapsed_seconds(str(meta.get("started_at") or "")),
        "pid": pid,
        "alive": alive,
        "exit_code": exit_code if exit_code is not None else "",
        "status": status,
        "stop_reason": stop_reason,
        "latest_progress_at": latest_progress_at,
        "latest_turn": latest_turn,
        "latest_tool_batch": latest_tool_batch,
        "mutation_status": mutation_status,
        "ida_status": ida_status,
        "summary": summary,
    }


def _emit_status_blocks(status_payload: Dict[str, Any], output_format: str, append_watch_log: bool = False) -> None:
    run_dir = Path(str(status_payload["run_dir"]))
    run_start = {
        "run_id": status_payload["run_id"],
        "target": status_payload["target"],
        "session_id": status_payload["session_id"],
        "request": status_payload["request"],
        "agent_profile": status_payload["agent_profile"],
        "started_at": status_payload["started_at"],
        "pid": status_payload["pid"],
    }
    heartbeat = {
        "elapsed_sec": status_payload["elapsed_sec"],
        "status": status_payload["status"],
        "latest_progress_at": status_payload["latest_progress_at"],
        "stalled": bool(status_payload.get("summary", {}).get("stalled", False)),
        "stalled_seconds": int(status_payload.get("summary", {}).get("stalled_seconds", 0) or 0),
    }
    turn_summary = status_payload.get("latest_turn", {}) or {}
    tool_batch = status_payload.get("latest_tool_batch", {}) or {}
    mutation_status = status_payload.get("mutation_status", {}) or {}
    ida_status = status_payload.get("ida_status", {}) or {}

    blocks: List[tuple[str, Dict[str, Any]]] = [("RUN_START", run_start), ("HEARTBEAT", heartbeat)]
    if turn_summary:
        blocks.append(("TURN_SUMMARY", turn_summary))
    if tool_batch:
        blocks.append(("TOOL_BATCH", tool_batch))
    if mutation_status:
        blocks.append(("MUTATION_STATUS", mutation_status))
    if ida_status:
        blocks.append(("IDA_STATUS", ida_status))
    if str(status_payload.get("status") or "") in {"stopped", "failed", "incomplete"}:
        blocks.append(
            (
                "STOP_SIGNAL",
                {
                    "reason": str(status_payload.get("stop_reason") or ""),
                    "status": str(status_payload.get("status") or ""),
                    "source": "runtime",
                },
            )
        )
    if not bool(status_payload.get("alive")):
        blocks.append(
            (
                "RUN_END",
                {
                    "exit_code": status_payload.get("exit_code", ""),
                    "stop_reason": str(status_payload.get("stop_reason") or status_payload.get("status") or ""),
                    "report_dir": str(run_dir / "report"),
                },
            )
        )

    for block_type, payload in blocks:
        _emit_stdout(block_type, payload, output_format)
        if append_watch_log:
            _append_watch_block(run_dir, block_type, payload)


def _build_reverse_command(args: argparse.Namespace, run_id: str, run_dir: Path, ida_port: int) -> List[str]:
    target = Path(str(args.target or args.input_path or "")).resolve()
    request_text = str(args.request or "").strip() or _default_request(args.agent_profile)
    agent_core = "dispatcher" if str(args.agent_profile or "") != "struct_recovery" else "struct_recovery"
    cmd = [
        str(sys.executable),
        str(PROJECT_ROOT / "reverse_agent.py"),
        "--request",
        request_text,
        "--input-path",
        str(target),
        "--ida-host",
        str(args.ida_host or "127.0.0.1"),
        "--ida-port",
        str(int(ida_port)),
        "--ida-log-dir",
        str(run_dir / "ida_service_logs"),
        "--service-wait-timeout",
        str(int(args.service_wait_timeout)),
        "--max-iterations",
        str(int(args.max_iterations)),
        "--agent-core",
        agent_core,
        "--agent-profile",
        str(args.agent_profile or "struct_recovery"),
        "--report-dir",
        str(run_dir / "report"),
    ]
    if str(args.idapython_kb_dir or "").strip():
        cmd.extend(["--idapython-kb-dir", str(args.idapython_kb_dir)])
    if str(args.case_id or "").strip():
        cmd.extend(["--case-id", str(args.case_id)])
    if str(args.case_spec_path or "").strip():
        cmd.extend(["--case-spec-path", str(args.case_spec_path)])
    for name in list(args.evidence_function or []):
        value = str(name or "").strip()
        if value:
            cmd.extend(["--evidence-function", value])
    if bool(args.no_run_auto_analysis):
        cmd.append("--no-run-auto-analysis")
    if bool(args.no_save_on_exit):
        cmd.append("--no-save-on-exit")
    if bool(args.ida_debug):
        cmd.append("--ida-debug")
    return cmd


def _start(args: argparse.Namespace) -> int:
    run_root = Path(str(args.run_root or DEFAULT_RUN_ROOT)).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    target = Path(str(args.target or args.input_path or "")).resolve()
    if not target.exists():
        print(f"ERROR run target not found: {target}")
        return 2

    run_id = f"run_{_now_stamp()}"
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    ida_host = str(args.ida_host or "127.0.0.1").strip() or "127.0.0.1"
    ida_port = _allocate_port(ida_host, int(args.ida_port or 5000))
    reverse_cmd = _build_reverse_command(args, run_id, run_dir, ida_port)
    exit_path = _exit_code_path(run_dir)
    stdout_path = _stdout_log_path(run_dir)
    stderr_path = _stderr_log_path(run_dir)

    shell_script = (
        f"cd {shlex.quote(str(PROJECT_ROOT))} && "
        f"export PYTHONPATH={shlex.quote(str(SRC_ROOT))}:\"${{PYTHONPATH:-}}\" && "
        f"export AGENT_WATCH_RUN_ID={shlex.quote(run_id)} && "
        f"export AGENT_SESSION_LOG_PATH={shlex.quote(str(_watch_log_path(run_dir)))} && "
        f"__watch_write_exit() {{ rc=\"$1\"; printf '%s\\n' \"$rc\" > {shlex.quote(str(exit_path))}; }}; "
        f"trap '__watch_write_exit 143; exit 143' TERM; "
        f"trap '__watch_write_exit 130; exit 130' INT; "
        f"{_shell_join(reverse_cmd)}"
        f" > {shlex.quote(str(stdout_path))} 2> {shlex.quote(str(stderr_path))}; "
        f"rc=$?; __watch_write_exit \"$rc\"; exit \"$rc\""
    )

    proc = subprocess.Popen(
        ["bash", "-lc", shell_script],
        cwd=str(PROJECT_ROOT),
        env=_build_env(),
        start_new_session=True,
    )

    meta = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "target": str(target),
        "request": str(args.request or "").strip() or _default_request(args.agent_profile),
        "agent_profile": str(args.agent_profile or "struct_recovery"),
        "started_at": _utc_now_iso(),
        "pid": int(proc.pid),
        "ida_host": ida_host,
        "ida_port": int(ida_port),
        "ida_url": f"http://{_service_access_host(ida_host)}:{int(ida_port)}",
        "command": reverse_cmd,
        "runner_command": ["bash", "-lc", shell_script],
        "observability_db": str(Path(str(args.db_path or DEFAULT_OBS_DB_PATH)).resolve()),
        "watch_log": str(_watch_log_path(run_dir)),
        "session_id": "",
    }
    _save_meta(meta)
    _append_watch_block(
        run_dir,
        "RUN_START",
        {
            "run_id": run_id,
            "target": str(target),
            "request": meta["request"],
            "agent_profile": meta["agent_profile"],
            "started_at": meta["started_at"],
            "pid": int(proc.pid),
            "ida_url": meta["ida_url"],
        },
    )

    if bool(args.background):
        _emit_stdout("RUN_START", {"run_id": run_id, "run_dir": str(run_dir), "pid": int(proc.pid)}, args.format)
        print(f"RUN_STARTED run_id={run_id}")
        return 0

    rc = proc.wait()
    _write_text(exit_path, str(int(rc)))
    status_payload = _build_status_payload(meta, run_root)
    _emit_status_blocks(status_payload, args.format, append_watch_log=True)
    return int(rc)


def _status(args: argparse.Namespace) -> int:
    run_root = Path(str(args.run_root or DEFAULT_RUN_ROOT)).resolve()
    meta = _load_meta(str(args.status or ""), run_root)
    _resolve_session_id(meta, run_root)
    status_payload = _build_status_payload(meta, run_root)
    _emit_status_blocks(status_payload, args.format, append_watch_log=True)
    return 0


def _tail(args: argparse.Namespace) -> int:
    run_root = Path(str(args.run_root or DEFAULT_RUN_ROOT)).resolve()
    meta = _load_meta(str(args.tail or ""), run_root)
    run_dir = Path(str(meta["run_dir"]))
    events = _fetch_event_tail(meta, run_root, max(1, int(args.lines or 20)))
    if events:
        for row in events[-max(1, int(args.lines or 20)):]:
            payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
            rendered = {
                "seq": int(row.get("seq", 0) or 0),
                "event": str(row.get("event", "") or ""),
                "created_at": str(row.get("created_at", "") or ""),
                "turn_id": str(payload.get("turn_id", "") or ""),
                "status": str(payload.get("status", "") or ""),
                "tool_name": str(payload.get("tool_name", "") or ""),
                "reason": str(payload.get("reason", "") or payload.get("error", "") or ""),
            }
            _emit_stdout("EVENT", rendered, args.format)
        return 0

    log_path = _watch_log_path(run_dir)
    if not log_path.exists():
        print(f"ERROR no events or watch log found for run: {meta['run_id']}")
        return 1
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines[-max(1, int(args.lines or 20)):]:
        print(line)
    return 0


def _stop(args: argparse.Namespace) -> int:
    run_root = Path(str(args.run_root or DEFAULT_RUN_ROOT)).resolve()
    meta = _load_meta(str(args.stop or ""), run_root)
    run_dir = Path(str(meta["run_dir"]))
    pid = int(meta.get("pid", 0) or 0)
    if pid <= 0:
        print(f"ERROR invalid pid for run: {meta['run_id']}")
        return 1

    if not _process_alive(pid):
        payload = {"run_id": str(meta["run_id"]), "reason": "already_stopped", "pid": pid}
        _append_watch_block(run_dir, "STOP_SIGNAL", payload)
        _emit_stdout("STOP_SIGNAL", payload, args.format)
        return 0

    stop_reason = "user_requested_stop"
    meta = _update_meta(
        run_dir,
        {
            "stop_requested_at": _utc_now_iso(),
            "stop_requested_reason": stop_reason,
        },
    )
    _append_session_event(
        meta,
        run_root,
        "run_stop_requested",
        {
            "reason": stop_reason,
            "source": "dev_run_watch",
            "pid": pid,
        },
    )
    _signal_group(pid, signal.SIGTERM)
    deadline = time.time() + max(1, int(args.stop_wait_sec or 20))
    while time.time() < deadline:
        if not _process_alive(pid):
            break
        time.sleep(0.5)
    forced_kill = False
    if _process_alive(pid):
        _signal_group(pid, signal.SIGKILL)
        forced_kill = True
        deadline = time.time() + 5
        while time.time() < deadline:
            if not _process_alive(pid):
                break
            time.sleep(0.2)

    exit_path = _exit_code_path(run_dir)
    if not exit_path.exists() and not _process_alive(pid):
        fallback_exit_code = 137 if forced_kill else 143
        _write_text(exit_path, str(int(fallback_exit_code)))

    payload = {
        "run_id": str(meta["run_id"]),
        "reason": stop_reason,
        "source": "dev_run_watch",
        "pid": pid,
    }
    _append_watch_block(run_dir, "STOP_SIGNAL", payload)
    _emit_stdout("STOP_SIGNAL", payload, args.format)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start/inspect/stop a watched reverse-agent run")
    parser.add_argument("--start", action="store_true", help="Start a new watched run")
    parser.add_argument("--status", default="", help="Show non-blocking status for a run_id")
    parser.add_argument("--tail", default="", help="Tail recent events for a run_id")
    parser.add_argument("--stop", default="", help="Stop a run_id")
    parser.add_argument("--background", action="store_true", help="Start run in background and return immediately")
    parser.add_argument("--target", default="", help="Target binary or IDB")
    parser.add_argument("--input-path", default="", help="Alias for --target")
    parser.add_argument("--request", default="", help="Reverse-analysis request text")
    parser.add_argument(
        "--agent-profile",
        choices=["struct_recovery", "attack_surface", "general_reverse"],
        default="struct_recovery",
        help="Reverse runtime profile",
    )
    parser.add_argument("--max-iterations", type=int, default=24)
    parser.add_argument("--ida-host", default="127.0.0.1")
    parser.add_argument("--ida-port", type=int, default=5000)
    parser.add_argument("--ida-debug", action="store_true")
    parser.add_argument("--service-wait-timeout", type=int, default=90)
    parser.add_argument("--idapython-kb-dir", default="")
    parser.add_argument("--case-id", default="")
    parser.add_argument("--case-spec-path", default="")
    parser.add_argument("--evidence-function", action="append", default=[])
    parser.add_argument("--db-path", default=str(DEFAULT_OBS_DB_PATH))
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--lines", type=int, default=20)
    parser.add_argument("--format", choices=["text", "jsonl"], default="text")
    parser.add_argument("--stop-wait-sec", type=int, default=20)
    parser.add_argument("--no-run-auto-analysis", action="store_true")
    parser.add_argument("--no-save-on-exit", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    action_count = sum(
        1
        for flag in [
            bool(args.start),
            bool(str(args.status or "").strip()),
            bool(str(args.tail or "").strip()),
            bool(str(args.stop or "").strip()),
        ]
        if flag
    )
    if action_count != 1:
        print("ERROR exactly one of --start/--status/--tail/--stop is required")
        return 2
    if args.start:
        return _start(args)
    if str(args.status or "").strip():
        return _status(args)
    if str(args.tail or "").strip():
        return _tail(args)
    return _stop(args)


if __name__ == "__main__":
    raise SystemExit(main())
