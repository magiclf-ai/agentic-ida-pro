#!/usr/bin/env python3
"""Pure-text full-system evaluation runner built on top of dev_run_watch."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from evaluation.cases import get_case, list_cases
from evaluation.ground_truth import (
    build_case_verdict_markdown,
    build_summary_markdown,
    judge_case,
    load_case_artifacts,
    load_case_spec_text,
    verdict_to_case_status,
)


DEFAULT_RUN_ROOT = PROJECT_ROOT / "logs" / "eval_runs"
DEFAULT_BASELINE_ROOT = PROJECT_ROOT / "logs" / "eval_baselines"
DEFAULT_DEV_WATCH = SRC_ROOT / "entrypoints" / "dev_run_watch.py"
STATE_DIRNAME = ".eval_state"
IDB_SUFFIXES = {".i64", ".idb", ".i32"}
DEFAULT_IDAT_PATH = Path("/home/hac425/ida-pro-9.3/idat")
IDA_PROFILE_FILES = ("ida.reg", "ida.hexlic", "ida-config.json", "proccache.lst")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8")


def _read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8", errors="ignore")


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _run_cmd(args: List[str], *, cwd: Path, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), env=env, text=True, capture_output=True, check=False)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _state_dir(eval_dir: Path) -> Path:
    return eval_dir / STATE_DIRNAME


def _status_markdown_path(eval_dir: Path) -> Path:
    return eval_dir / "status.md"


def _case_state_dir(case_dir: Path) -> Path:
    return case_dir / STATE_DIRNAME


def _case_input_dir(case_dir: Path) -> Path:
    return _case_state_dir(case_dir) / "input"


def _case_dev_run_dir(case_dir: Path) -> Path:
    return _case_state_dir(case_dir) / "dev_run"


def _case_raw_log_path(case_dir: Path, name: str) -> Path:
    return _case_state_dir(case_dir) / name


def _current_case_path(eval_dir: Path) -> Path:
    return _state_dir(eval_dir) / "current_case.txt"


def _current_watch_run_path(eval_dir: Path) -> Path:
    return _state_dir(eval_dir) / "current_watch_run_id.txt"


def _stop_request_path(eval_dir: Path) -> Path:
    return _state_dir(eval_dir) / "stop_request.json"


def _run_metadata_path(eval_dir: Path) -> Path:
    return _state_dir(eval_dir) / "run_metadata.json"


def _background_meta_path(eval_dir: Path) -> Path:
    return _state_dir(eval_dir) / "background_meta.json"


def _request_stop(eval_dir: Path, reason: str) -> None:
    _write_json(
        _stop_request_path(eval_dir),
        {
            "requested_at": _utc_now_iso(),
            "reason": str(reason or "").strip() or "user_requested_stop",
        },
    )


def _is_idb_path(path: Path) -> bool:
    return path.suffix.lower() in IDB_SUFFIXES


def _resolve_idat_path() -> Path:
    configured = str(os.environ.get("IDA_BATCH_EXE", "")).strip()
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.append(DEFAULT_IDAT_PATH)
    which_path = shutil.which("idat")
    if which_path:
        candidates.append(Path(which_path))
    for candidate in candidates:
        if candidate and candidate.exists() and candidate.is_file():
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates if str(path))
    raise RuntimeError(f"IDA batch executable not found. searched={searched}")


def _copy_ida_profile(home_dir: Path) -> Path:
    source_dir = Path.home() / ".idapro"
    target_dir = home_dir / ".idapro"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in IDA_PROFILE_FILES:
        src = source_dir / name
        if src.exists() and src.is_file():
            shutil.copy2(src, target_dir / name)
    if not (target_dir / "ida.reg").exists():
        raise RuntimeError(f"IDA license acceptance file missing: {source_dir / 'ida.reg'}")
    return target_dir


def _generate_fresh_idb_for_case(binary_path: Path, case_dir: Path) -> Path:
    idat_path = _resolve_idat_path()
    idb_path = binary_path.with_suffix(".i64")
    asm_path = binary_path.with_suffix(".asm")
    log_path = _case_raw_log_path(case_dir, "idat_import.log")
    home_dir = _case_state_dir(case_dir) / "idat_home"
    _copy_ida_profile(home_dir)
    if idb_path.exists():
        idb_path.unlink()
    if asm_path.exists():
        asm_path.unlink()
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    proc = _run_cmd(
        [
            str(idat_path),
            "-B",
            f"-L{log_path}",
            f"-o{idb_path}",
            str(binary_path),
        ],
        cwd=idat_path.parent,
        env=env,
    )
    _write_text(_case_raw_log_path(case_dir, "idat_stdout.log"), proc.stdout)
    _write_text(_case_raw_log_path(case_dir, "idat_stderr.log"), proc.stderr)
    if proc.returncode != 0 or not idb_path.exists():
        raise RuntimeError(
            f"fresh IDB generation failed rc={proc.returncode} binary={binary_path} log={log_path}"
        )
    return idb_path


def _prepare_input_for_case(case: Dict[str, Any], case_dir: Path) -> Path:
    src = Path(str(case["input_path"])).resolve()
    input_dir = _case_input_dir(case_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    copied_input = input_dir / src.name
    shutil.copy2(src, copied_input)
    analysis_target = copied_input
    generated_idb = ""
    if not _is_idb_path(copied_input):
        analysis_target = _generate_fresh_idb_for_case(copied_input, case_dir)
        generated_idb = str(analysis_target)
    _write_json(
        _case_state_dir(case_dir) / "prepared_input.json",
        {
            "original_input": str(src),
            "copied_input": str(copied_input),
            "analysis_target": str(analysis_target),
            "generated_idb": generated_idb,
        },
    )
    return analysis_target


def _find_latest_service_log(ida_log_dir: Path) -> Optional[Path]:
    if not ida_log_dir.exists():
        return None
    logs = sorted(ida_log_dir.glob("ida_service_*.log"))
    return logs[-1] if logs else None


def _start_dev_watch(case: Dict[str, Any], isolated_input: Path, case_dir: Path) -> str:
    watch_root = _case_dev_run_dir(case_dir)
    cmd = [
        str(sys.executable),
        str(DEFAULT_DEV_WATCH),
        "--start",
        "--target",
        str(isolated_input),
        "--agent-profile",
        str(case["profile"]),
        "--request",
        str(case["request_text"]),
        "--max-iterations",
        str(int(case["max_iterations"])),
        "--run-root",
        str(watch_root),
        "--background",
        "--case-id",
        str(case["case_id"]),
        "--case-spec-path",
        str(case.get("spec_path", "") or ""),
    ]
    for name in list(case.get("evidence_functions", []) or []):
        value = str(name or "").strip()
        if value:
            cmd.extend(["--evidence-function", value])
    proc = _run_cmd(cmd, cwd=PROJECT_ROOT, env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(f"dev_run_watch start failed: {proc.stderr or proc.stdout}")
    match = re.search(r"RUN_STARTED run_id=([^\s]+)", proc.stdout)
    if not match:
        raise RuntimeError(f"failed to parse watch run id: {proc.stdout}")
    return str(match.group(1))


def _watch_status(case_dir: Path, watch_run_id: str) -> Dict[str, Any]:
    cmd = [
        str(sys.executable),
        str(DEFAULT_DEV_WATCH),
        "--status",
        str(watch_run_id),
        "--run-root",
        str(_case_dev_run_dir(case_dir)),
        "--format",
        "jsonl",
    ]
    proc = _run_cmd(cmd, cwd=PROJECT_ROOT, env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(f"dev_run_watch status failed: {proc.stderr or proc.stdout}")
    blocks: List[Dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        row = str(line or "").strip()
        if not row.startswith("{"):
            continue
        try:
            blocks.append(json.loads(row))
        except Exception:
            continue
    status: Dict[str, Any] = {"blocks": blocks}
    for block in blocks:
        block_type = str(block.get("type") or "")
        status[block_type] = block
    return status


def _wait_watch(case: Dict[str, Any], case_dir: Path, watch_run_id: str) -> Dict[str, Any]:
    deadline = time.time() + int(case["max_runtime_sec"])
    latest: Dict[str, Any] = {}
    while time.time() < deadline:
        latest = _watch_status(case_dir, watch_run_id)
        if "RUN_END" in latest:
            return latest
        time.sleep(5)
    stop_cmd = [
        str(sys.executable),
        str(DEFAULT_DEV_WATCH),
        "--stop",
        str(watch_run_id),
        "--run-root",
        str(_case_dev_run_dir(case_dir)),
    ]
    _run_cmd(stop_cmd, cwd=PROJECT_ROOT, env=os.environ.copy())
    return _watch_status(case_dir, watch_run_id)


def _copy_watch_outputs(case_dir: Path, watch_run_id: str) -> None:
    watch_dir = _case_dev_run_dir(case_dir) / watch_run_id
    mapping = {
        watch_dir / "stdout.log": _case_raw_log_path(case_dir, "stdout.log"),
        watch_dir / "stderr.log": _case_raw_log_path(case_dir, "stderr.log"),
        watch_dir / "watch.log": _case_raw_log_path(case_dir, "watch.log"),
    }
    for src, dst in mapping.items():
        if src.exists():
            shutil.copy2(src, dst)

    service_log = _find_latest_service_log(watch_dir / "ida_service_logs")
    if service_log is not None:
        shutil.copy2(service_log, _case_raw_log_path(case_dir, "service.log"))

    report_root = watch_dir / "report"
    if not report_root.exists():
        return
    report_dir = report_root
    report_children = sorted([row for row in report_root.iterdir() if row.is_dir()])
    if report_children:
        report_dir = report_children[-1]
    for name in ("run_trace.md", "evidence.md"):
        src = report_dir / name
        if src.exists():
            shutil.copy2(src, case_dir / name)


def _watch_summary_from_status(watch_status: Dict[str, Any]) -> Dict[str, str]:
    heartbeat = watch_status.get("HEARTBEAT", {}) if isinstance(watch_status, dict) else {}
    stop_signal = watch_status.get("STOP_SIGNAL", {}) if isinstance(watch_status, dict) else {}
    run_end = watch_status.get("RUN_END", {}) if isinstance(watch_status, dict) else {}
    return {
        "run_status": str(heartbeat.get("status", "") or ""),
        "stop_reason": str(run_end.get("stop_reason", "") or stop_signal.get("reason", "") or ""),
    }


def _read_run_exit_code(run_dir: Path) -> int:
    path = run_dir / "exit_code.txt"
    if not path.exists():
        return 1
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return 1


def _case_runtime_meta_path(case_dir: Path) -> Path:
    return _case_state_dir(case_dir) / "runtime.json"


def _write_case_runtime_meta(case_dir: Path, *, run_exit_code: int, run_status: str, stop_reason: str) -> None:
    _write_json(
        _case_runtime_meta_path(case_dir),
        {
            "run_exit_code": int(run_exit_code),
            "run_status": str(run_status or ""),
            "stop_reason": str(stop_reason or ""),
        },
    )


def _load_case_runtime_meta(case_dir: Path) -> Dict[str, Any]:
    meta = _read_json(_case_runtime_meta_path(case_dir), {})
    if meta:
        return meta

    watch_root = _case_dev_run_dir(case_dir)
    watch_dirs = sorted([row for row in watch_root.glob("run_*") if row.is_dir()]) if watch_root.exists() else []
    if watch_dirs:
        latest_run = watch_dirs[-1]
        run_exit_code = _read_run_exit_code(latest_run)
        try:
            watch_status = _watch_status(case_dir, latest_run.name)
            watch_summary = _watch_summary_from_status(watch_status)
            return {
                "run_exit_code": int(run_exit_code),
                "run_status": str(watch_summary.get("run_status", "") or ""),
                "stop_reason": str(watch_summary.get("stop_reason", "") or ""),
            }
        except Exception:
            pass

    verdict_text = _read_text(case_dir / "verdict.md")
    if not verdict_text.strip():
        return {}
    patterns = {
        "run_exit_code": r"^- run_exit_code:\s*(.+?)\s*$",
        "run_status": r"^- run_status:\s*(.+?)\s*$",
        "stop_reason": r"^- stop_reason:\s*(.*?)\s*$",
    }
    parsed: Dict[str, Any] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, verdict_text, flags=re.MULTILINE)
        if not match:
            continue
        value = str(match.group(1) or "").strip()
        if key == "run_exit_code":
            try:
                parsed[key] = int(value)
            except Exception:
                continue
        else:
            parsed[key] = value
    return parsed


def _case_infra_payload(case: Dict[str, Any], run_exit_code: int, run_status: str, stop_reason: str, summary: str, evidence: str) -> Dict[str, str]:
    return {
        "verdict": "infra_fail",
        "summary": summary,
        "evidence": evidence,
        "risks": f"- run_exit_code: {int(run_exit_code)}\n- run_status: {run_status}\n- stop_reason: {stop_reason}",
    }


def _run_case(case: Dict[str, Any], case_dir: Path) -> Dict[str, Any]:
    isolated_input = _prepare_input_for_case(case, case_dir)
    watch_run_id = _start_dev_watch(case, isolated_input, case_dir)
    _write_text(_current_watch_run_path(case_dir.parent), watch_run_id)
    watch_status = _wait_watch(case, case_dir, watch_run_id)
    run_dir = _case_dev_run_dir(case_dir) / watch_run_id
    run_exit_code = _read_run_exit_code(run_dir)
    meta = _read_json(run_dir / "meta.json", {})
    _copy_watch_outputs(case_dir, watch_run_id)

    watch_summary = _watch_summary_from_status(watch_status)
    run_status = str(watch_summary.get("run_status", "") or "")
    stop_reason = str(watch_summary.get("stop_reason", "") or "")
    session_id = str(meta.get("session_id", "") or "")
    _write_case_runtime_meta(case_dir, run_exit_code=run_exit_code, run_status=run_status, stop_reason=stop_reason)

    artifacts = load_case_artifacts(str(case_dir))
    spec_text = load_case_spec_text(str(case.get("spec_path", "") or ""))

    if not artifacts["run_trace"] or not artifacts["evidence"]:
        verdict_payload = _case_infra_payload(
            case,
            run_exit_code,
            run_status,
            stop_reason,
            "missing evidence artifacts",
            "- run_trace.md or evidence.md is missing",
        )
    else:
        try:
            verdict_payload = judge_case(
                case_id=str(case["case_id"]),
                profile=str(case["profile"]),
                case_spec_text=spec_text,
                run_trace_text=artifacts["run_trace"],
                evidence_text=artifacts["evidence"],
                run_exit_code=run_exit_code,
                run_status=run_status,
                stop_reason=stop_reason,
            )
        except Exception as e:
            verdict_payload = _case_infra_payload(
                case,
                run_exit_code,
                run_status,
                stop_reason,
                f"judge failed: {e}",
                "- judge invocation failed",
            )

    verdict_md = build_case_verdict_markdown(
        case_id=str(case["case_id"]),
        profile=str(case["profile"]),
        run_exit_code=run_exit_code,
        run_status=run_status,
        stop_reason=stop_reason,
        verdict_payload=verdict_payload,
    )
    _write_text(case_dir / "verdict.md", verdict_md)

    return {
        "case_id": str(case["case_id"]),
        "profile": str(case["profile"]),
        "status": verdict_to_case_status(str(verdict_payload.get("verdict", "") or "")),
        "verdict": str(verdict_payload.get("verdict", "") or ""),
        "summary": str(verdict_payload.get("summary", "") or ""),
        "run_exit_code": int(run_exit_code),
        "run_status": run_status,
        "stop_reason": stop_reason,
        "watch_run_id": watch_run_id,
        "session_id": session_id,
        "case_dir": str(case_dir),
    }




def _list_cases(args: argparse.Namespace) -> int:
    cases = list_cases()
    for c in cases:
        print(f"case_id={c['case_id']}  profile={c['profile']}  max_iterations={c['max_iterations']}  max_runtime_sec={c['max_runtime_sec']}")
    return 0


def _list_suites(args: argparse.Namespace) -> int:
    from evaluation.cases import SUITES
    for suite_name, case_ids in SUITES.items():
        print(f"suite={suite_name}  cases={','.join(case_ids)}")
    return 0


def _case_info(args: argparse.Namespace) -> int:
    case_id = str(args.case_info or "").strip()
    case = get_case(case_id)
    for key, value in case.items():
        print(f"  {key}: {value}")
    return 0


def _run_single_case(args: argparse.Namespace) -> int:
    case_id = str(args.case or "").strip()
    case = get_case(case_id)
    run_root = Path(str(args.run_root or DEFAULT_RUN_ROOT)).resolve()
    run_id = f"eval_{_stamp()}"
    eval_dir = run_root / run_id
    eval_dir.mkdir(parents=True, exist_ok=True)
    case_dir = eval_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        _run_metadata_path(eval_dir),
        {
            "run_id": run_id,
            "case_ids": [case_id],
            "started_at": _utc_now_iso(),
        },
    )
    try:
        result = _run_case(case, case_dir)
    except Exception as e:
        verdict_payload = {
            "verdict": "infra_fail",
            "summary": f"runner exception: {e}",
            "evidence": "- case execution raised an exception before verdict generation",
            "risks": "",
        }
        _write_text(
            case_dir / "verdict.md",
            build_case_verdict_markdown(
                case_id=case_id,
                profile=str(case["profile"]),
                run_exit_code=1,
                run_status="failed",
                stop_reason="runner_exception",
                verdict_payload=verdict_payload,
            ),
        )
        result = {
            "case_id": case_id,
            "verdict": "infra_fail",
            "summary": str(verdict_payload["summary"]),
            "run_exit_code": 1,
            "case_dir": str(case_dir),
        }
    verdict = str(result.get("verdict", "") or "")
    run_exit_code = int(result.get("run_exit_code", 1))
    summary = str(result.get("summary", "") or "")
    print(f"CASE_DONE case_id={case_id} verdict={verdict} run_exit_code={run_exit_code} run_id={run_id} case_dir={case_dir}")
    if summary:
        print(f"  summary: {summary}")
    if str(args.save_baseline or "").strip():
        _save_baseline(run_id, eval_dir, str(args.save_baseline).strip())
    return 0 if verdict == "pass" else 1


def _save_baseline(run_id: str, eval_dir: Path, label: str) -> None:
    baseline_root = DEFAULT_BASELINE_ROOT.resolve()
    baseline_root.mkdir(parents=True, exist_ok=True)
    target = baseline_root / str(label).strip()
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(eval_dir, target)
    metadata = {
        "label": str(label),
        "run_id": run_id,
        "saved_at": _utc_now_iso(),
    }
    _write_json(target / "metadata.json", metadata)
    latest = baseline_root / "latest"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(target.name)




def _status(args: argparse.Namespace) -> int:
    run_dir = Path(str(args.status or "")).resolve()
    if not run_dir.exists():
        run_dir = Path(str(args.run_root or DEFAULT_RUN_ROOT)).resolve() / str(args.status or "").strip()
    status_md = _status_markdown_path(run_dir)
    if status_md.exists():
        print(_read_text(status_md).rstrip())
        return 0
    summary_md = run_dir / "summary.md"
    progress_md = run_dir / "progress.md"
    if summary_md.exists():
        print(_read_text(summary_md).rstrip())
        return 0
    if progress_md.exists():
        print(_read_text(progress_md).rstrip())
        return 0
    print(f"ERROR run_dir not found or no status markdown: {run_dir}")
    return 2


def _stop(args: argparse.Namespace) -> int:
    run_dir = Path(str(args.stop or "")).resolve()
    if not run_dir.exists():
        run_dir = Path(str(args.run_root or DEFAULT_RUN_ROOT)).resolve() / str(args.stop or "").strip()
    meta = _read_json(_background_meta_path(run_dir), _read_json(run_dir / "background_meta.json", {}))
    _request_stop(run_dir, "user_requested_stop")

    current_case_id = _read_text(_current_case_path(run_dir)).strip()
    current_watch_run_id = _read_text(_current_watch_run_path(run_dir)).strip()
    if current_case_id and not current_watch_run_id:
        watch_root = _case_dev_run_dir(run_dir / current_case_id)
        watch_dirs = sorted([row for row in watch_root.glob("run_*") if row.is_dir()]) if watch_root.exists() else []
        if watch_dirs:
            current_watch_run_id = watch_dirs[-1].name
    if current_case_id and current_watch_run_id:
        current_case_dir = run_dir / current_case_id
        _run_cmd(
            [
                str(sys.executable),
                str(DEFAULT_DEV_WATCH),
                "--stop",
                current_watch_run_id,
                "--run-root",
                str(_case_dev_run_dir(current_case_dir)),
            ],
            cwd=PROJECT_ROOT,
            env=os.environ.copy(),
        )

    pid = int(meta.get("pid", 0) or 0)
    if pid > 0 and _process_alive(pid):
        deadline = time.time() + 30
        while time.time() < deadline:
            if not _process_alive(pid):
                break
            if _status_markdown_path(run_dir).exists() or (run_dir / "summary.md").exists():
                break
            time.sleep(1)
        if _process_alive(pid) and not _status_markdown_path(run_dir).exists() and not (run_dir / "summary.md").exists():
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                pass
    print(f"EVAL_STOPPED run_id={run_dir.name}")
    return 0


def _judge_only(args: argparse.Namespace) -> int:
    run_dir = Path(str(args.judge_only or "")).resolve()
    if not run_dir.exists():
        run_dir = Path(str(args.run_root or DEFAULT_RUN_ROOT)).resolve() / str(args.judge_only or "").strip()
    metadata = _read_json(_run_metadata_path(run_dir), _read_json(run_dir / "metadata.json", {}))
    case_ids = list(metadata.get("case_ids", []) or [])
    results: List[Dict[str, Any]] = []
    for case_id in case_ids:
        case = get_case(str(case_id))
        case_dir = run_dir / str(case_id)
        artifacts = load_case_artifacts(str(case_dir))
        runtime_meta = _load_case_runtime_meta(case_dir)
        raw_run_exit_code = runtime_meta.get("run_exit_code", 1)
        if raw_run_exit_code in (None, ""):
            raw_run_exit_code = 1
        run_exit_code = int(raw_run_exit_code)
        run_status = str(runtime_meta.get("run_status", "") or "")
        stop_reason = str(runtime_meta.get("stop_reason", "") or "")
        verdict_payload = judge_case(
            case_id=str(case["case_id"]),
            profile=str(case["profile"]),
            case_spec_text=load_case_spec_text(str(case["spec_path"])),
            run_trace_text=artifacts["run_trace"],
            evidence_text=artifacts["evidence"],
            run_exit_code=run_exit_code,
            run_status=run_status,
            stop_reason=stop_reason,
        )
        _write_text(
            case_dir / "verdict.md",
            build_case_verdict_markdown(
                case_id=str(case["case_id"]),
                profile=str(case["profile"]),
                run_exit_code=run_exit_code,
                run_status=run_status,
                stop_reason=stop_reason,
                verdict_payload=verdict_payload,
            ),
        )
        results.append(
            {
                "case_id": str(case["case_id"]),
                "profile": str(case["profile"]),
                "status": verdict_to_case_status(str(verdict_payload.get("verdict", "") or "")),
                "verdict": str(verdict_payload.get("verdict", "") or ""),
                "summary": str(verdict_payload.get("summary", "") or ""),
                "run_exit_code": run_exit_code,
                "run_status": run_status,
                "stop_reason": stop_reason,
                "watch_run_id": "",
                "session_id": "",
                "case_dir": str(case_dir),
            }
        )
    run_id = str(metadata.get("run_id", run_dir.name) or run_dir.name)
    summary_md = build_summary_markdown(run_id=run_id, results=results, stop_requested=False, stop_reason="")
    _write_text(_status_markdown_path(run_dir), summary_md)
    print(f"JUDGE_ONLY_DONE run_dir={run_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single-case evaluation runner")
    parser.add_argument("--case", default="", help="Run a single case by case_id")
    parser.add_argument("--list-cases", action="store_true", help="List all registered cases")
    parser.add_argument("--list-suites", action="store_true", help="List all suites and their case_ids")
    parser.add_argument("--case-info", default="", help="Show full metadata for a case_id")
    parser.add_argument("--judge-only", default="", help="Re-judge an existing run directory")
    parser.add_argument("--save-baseline", default="", help="Save run as a named baseline")
    parser.add_argument("--status", default="", help="Show status of a run directory")
    parser.add_argument("--stop", default="", help="Stop a running eval")
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT), help="Root directory for eval runs")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.list_cases):
        return _list_cases(args)
    if bool(args.list_suites):
        return _list_suites(args)
    if str(args.case_info or "").strip():
        return _case_info(args)
    if str(args.judge_only or "").strip():
        return _judge_only(args)
    if str(args.status or "").strip():
        return _status(args)
    if str(args.stop or "").strip():
        return _stop(args)
    if str(args.case or "").strip():
        return _run_single_case(args)
    print("ERROR one of --case/--list-cases/--list-suites/--case-info/--judge-only/--status/--stop is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
