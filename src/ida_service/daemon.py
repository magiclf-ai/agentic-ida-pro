#!/usr/bin/env python3
"""
IDA Service Daemon
- 支持运行时打开/关闭数据库
- 串行执行所有 IDA 脚本（主线程约束）
"""
import argparse
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, request

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ida_service import config
from ida_service.executor import execute_script
from ida_service.search_core import search_symbols_strings, search_xrefs

# 直接导入 IDA 模块（daemon 运行在 IDA 进程中）
try:
    import idapro
    import ida_idaapi
    import idaapi
    import idc
    import idautils
    import ida_funcs
    import ida_hexrays
    import ida_lines
    HAS_IDA = True
except ImportError:
    HAS_IDA = False

app = Flask(__name__)
logger = logging.getLogger("ida_service")
_exec_lock = threading.RLock()
_db_state: Dict[str, Any] = {
    "opened": False,
    "path": None,
    "opened_at": None,
}
_service_state: Dict[str, Any] = {
    "is_executing": False,
    "current_script": "",
    "current_script_started_at": None,
    "last_script": "",
    "last_duration_ms": 0,
    "last_execute_success": None,
    "last_error": "",
    "last_event": "",
    "last_event_at": None,
    "timeout_count": 0,
    "execute_count": 0,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _script_preview(code: str, limit: int = 160) -> str:
    text = " ".join(str(code or "").split())
    return text[:limit]


def _emit_service_event(event: str, **payload: Any) -> None:
    _service_state["last_event"] = str(event or "").strip()
    _service_state["last_event_at"] = _utc_now_iso()
    data = {"event": str(event or "").strip(), **payload, "at": _service_state["last_event_at"]}
    logger.info("EVENT %s", json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _service_status_snapshot() -> Dict[str, Any]:
    return {
        "is_executing": bool(_service_state["is_executing"]),
        "current_script": str(_service_state["current_script"] or ""),
        "current_script_started_at": _service_state["current_script_started_at"],
        "last_script": str(_service_state["last_script"] or ""),
        "last_duration_ms": int(_service_state["last_duration_ms"] or 0),
        "last_execute_success": _service_state["last_execute_success"],
        "last_error": str(_service_state["last_error"] or ""),
        "last_event": str(_service_state["last_event"] or ""),
        "last_event_at": _service_state["last_event_at"],
        "timeout_count": int(_service_state["timeout_count"] or 0),
        "execute_count": int(_service_state["execute_count"] or 0),
        "db_opened": bool(_db_state["opened"]),
        "db_path": _db_state["path"],
        "db_opened_at": _db_state["opened_at"],
    }


def setup_logging(log_dir: str, debug: bool = False) -> str:
    """设置日志系统"""
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"ida_service_{timestamp}.log")

    level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(level)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info("Logging to: %s", log_file)
    return log_file


def _check_ida_available() -> bool:
    """检查 IDA 模块是否可用"""
    return HAS_IDA


def _execute_serialized(code: str, context: Optional[Dict] = None) -> Tuple[Dict, float]:
    """串行执行脚本，避免并发调用 IDA SDK。"""
    with _exec_lock:
        started = time.time()
        preview = _script_preview(code)
        _service_state["is_executing"] = True
        _service_state["current_script"] = preview
        _service_state["current_script_started_at"] = _utc_now_iso()
        _service_state["last_script"] = preview
        _service_state["execute_count"] = int(_service_state["execute_count"] or 0) + 1
        _service_state["last_error"] = ""
        _emit_service_event(
            "execute_start",
            script_preview=preview,
            context_keys=sorted((context or {}).keys())[:12],
            timeout_sec=int(config.SCRIPT_TIMEOUT),
        )
        result: Dict[str, Any] = {}
        success = False
        error_text = ""
        try:
            result = execute_script(code, context)
            success = bool(result.get("success", False))
            error_text = str(result.get("error") or result.get("stderr") or "")
            return result, time.time() - started
        except Exception as e:
            error_text = str(e)
            raise
        finally:
            elapsed = time.time() - started
            timeout_hit = elapsed > config.SCRIPT_TIMEOUT
            _service_state["is_executing"] = False
            _service_state["current_script"] = ""
            _service_state["current_script_started_at"] = None
            _service_state["last_duration_ms"] = int(elapsed * 1000)
            _service_state["last_execute_success"] = bool(success)
            _service_state["last_error"] = error_text[:800]
            if timeout_hit:
                _service_state["timeout_count"] = int(_service_state["timeout_count"] or 0) + 1
                logger.warning(
                    "Script exceeded configured timeout: %.2fs > %ss",
                    elapsed,
                    config.SCRIPT_TIMEOUT,
                )
                _emit_service_event(
                    "execute_timeout",
                    script_preview=preview,
                    duration_ms=int(elapsed * 1000),
                    timeout_sec=int(config.SCRIPT_TIMEOUT),
                )
            _emit_service_event(
                "execute_end",
                script_preview=preview,
                success=bool(success),
                duration_ms=int(elapsed * 1000),
                error=error_text[:400],
            )


def _require_db_opened():
    if not _db_state["opened"]:
        return (
            jsonify({"success": False, "error": "Database not opened. Call /db/open first."}),
            503,
        )
    return None


def _normalize_path(path: str) -> str:
    return os.path.abspath(str(path or "").strip())


def _is_idb_path(path: str) -> bool:
    suffix = os.path.splitext(str(path or ""))[1].lower()
    return suffix in {".i64", ".idb", ".i32"}


def _default_idb_path_for_binary(binary_path: str) -> str:
    normalized = _normalize_path(binary_path)
    folder = os.path.dirname(normalized) or "."
    base = os.path.basename(normalized)
    stem, _ext = os.path.splitext(base)
    if not stem:
        stem = base
    return _normalize_path(os.path.join(folder, f"{stem}.i64"))


def _save_current_database_as(target_path: str) -> str:
    normalized = _normalize_path(target_path)
    folder = os.path.dirname(normalized) or "."
    os.makedirs(folder, exist_ok=True)
    script = r'''
import idc
target_path = str(target_path or "").strip()
if not target_path:
    __result__ = {"success": False, "error": "empty target_path"}
else:
    ok = bool(idc.save_database(target_path, 0))
    __result__ = {
        "success": bool(ok),
        "path": str(idc.get_idb_path() or target_path).strip(),
        "error": "" if ok else f"save_database failed: {target_path}",
    }
'''
    result, _elapsed = _execute_serialized(script, {"target_path": normalized})
    if not bool(result.get("success")):
        raise RuntimeError(str(result.get("error") or "save_database script failed"))
    payload = result.get("result")
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected save_database payload: {type(payload).__name__}")
    if not bool(payload.get("success")):
        raise RuntimeError(str(payload.get("error") or f"save_database failed: {normalized}"))
    return str(payload.get("path") or normalized).strip()


def _set_db_opened(path: str):
    _db_state["opened"] = True
    _db_state["path"] = str(path or "").strip()
    _db_state["opened_at"] = datetime.now(timezone.utc).isoformat()


def _set_db_closed():
    _db_state["opened"] = False
    _db_state["path"] = None
    _db_state["opened_at"] = None


def _cleanup_unpacked_idb_files_in_dir(input_path: str) -> Dict[str, Any]:
    directory = os.path.dirname(_normalize_path(input_path))
    if not directory:
        directory = "."
    if not os.path.isdir(directory):
        return {"directory": directory, "deleted_count": 0, "deleted_files": []}

    targets = (".id0", ".id1", ".id2", ".nam", ".til")
    deleted_files = []
    for name in os.listdir(directory):
        lower = str(name).lower()
        if not lower.endswith(targets):
            continue
        full_path = os.path.join(directory, name)
        if not os.path.isfile(full_path):
            continue
        try:
            os.remove(full_path)
            deleted_files.append(full_path)
        except Exception as e:
            logger.warning("Failed to delete unpacked file: %s (%s)", full_path, e)
    if deleted_files:
        logger.info("Deleted %d unpacked IDB sidecar files in %s", len(deleted_files), directory)
    return {"directory": directory, "deleted_count": len(deleted_files), "deleted_files": deleted_files}


def _close_database_locked(save: bool = True) -> Dict[str, Any]:
    if not _check_ida_available():
        raise RuntimeError("IDA modules unavailable")

    if not _db_state["opened"]:
        return {
            "already_closed": True,
            "closed_path": "",
            "saved": bool(save),
        }

    started = time.time()
    closed_path = str(idc.get_idb_path() or _db_state.get("path") or "").strip()
    _emit_service_event("db_close_start", path=closed_path, save=bool(save))
    try:
        idapro.close_database(bool(save))
        _set_db_closed()
        elapsed = time.time() - started
        logger.info("Database closed in %.2fs: %s (saved=%s)", elapsed, closed_path or "unknown", bool(save))
        _emit_service_event(
            "db_close_end",
            path=closed_path,
            save=bool(save),
            duration_ms=int(elapsed * 1000),
        )
        return {
            "already_closed": False,
            "closed_path": closed_path,
            "saved": bool(save),
        }
    except Exception as e:
        _service_state["last_error"] = str(e)[:800]
        _emit_service_event("db_close_failed", path=closed_path, save=bool(save), error=str(e)[:400])
        raise


def _open_database_locked(
    input_path: str,
    run_auto_analysis: bool = True,
    save_current: bool = True,
) -> Dict[str, Any]:
    if not _check_ida_available():
        raise RuntimeError("IDA modules unavailable")

    if not input_path:
        raise RuntimeError("Missing input path.")

    normalized_input_path = _normalize_path(input_path)
    if not os.path.exists(normalized_input_path):
        raise RuntimeError(f"Input file not found: {normalized_input_path}")
    _emit_service_event(
        "db_open_start",
        input_path=normalized_input_path,
        run_auto_analysis=bool(run_auto_analysis),
        save_current=bool(save_current),
    )

    try:
        resolved_open_path = normalized_input_path
        if not _is_idb_path(normalized_input_path):
            preferred_idb = _default_idb_path_for_binary(normalized_input_path)
            if os.path.exists(preferred_idb):
                resolved_open_path = preferred_idb

        switched_from = ""
        close_result = None
        already_open = False

        if _db_state["opened"]:
            current_path = str(idc.get_idb_path() or _db_state.get("path") or "").strip()
            if current_path and _normalize_path(current_path) == _normalize_path(resolved_open_path):
                already_open = True
            else:
                switched_from = current_path
                close_result = _close_database_locked(save=bool(save_current))

        if already_open:
            _emit_service_event(
                "db_open_end",
                input_path=normalized_input_path,
                resolved_open_path=resolved_open_path,
                active_path=str(_db_state.get("path") or ""),
                duration_ms=0,
                run_auto_analysis=bool(run_auto_analysis),
                already_open=True,
            )
            return {
                "path": str(_db_state.get("path") or ""),
                "opened_at": str(_db_state.get("opened_at") or ""),
                "already_open": True,
                "switched_from": "",
                "close_result": None,
                "normalized_input_path": normalized_input_path,
                "resolved_open_path": resolved_open_path,
                "initialized_idb_path": "",
                "cleanup_result": {"directory": os.path.dirname(resolved_open_path), "deleted_count": 0, "deleted_files": []},
            }

        cleanup_result = {"directory": os.path.dirname(resolved_open_path), "deleted_count": 0, "deleted_files": []}
        if not _is_idb_path(resolved_open_path):
            cleanup_result = _cleanup_unpacked_idb_files_in_dir(resolved_open_path)

        started = time.time()
        open_ret = idapro.open_database(resolved_open_path, bool(run_auto_analysis))
        if bool(open_ret):
            raise RuntimeError(f"idapro.open_database failed with code: {open_ret} (path={resolved_open_path})")
        active_path = str(idc.get_idb_path() or resolved_open_path).strip()

        initialized_idb_path = ""
        if not _is_idb_path(resolved_open_path):
            preferred_idb = _default_idb_path_for_binary(normalized_input_path)
            if _normalize_path(active_path) != _normalize_path(preferred_idb):
                active_path = _save_current_database_as(preferred_idb)
            initialized_idb_path = _normalize_path(preferred_idb)

        _set_db_opened(active_path)
        elapsed = time.time() - started
        logger.info(
            "Database opened in %.2fs: %s (run_auto_analysis=%s)",
            elapsed,
            active_path,
            bool(run_auto_analysis),
        )
        _emit_service_event(
            "db_open_end",
            input_path=normalized_input_path,
            resolved_open_path=resolved_open_path,
            active_path=active_path,
            duration_ms=int(elapsed * 1000),
            run_auto_analysis=bool(run_auto_analysis),
            already_open=False,
        )
        return {
            "path": active_path,
            "opened_at": str(_db_state.get("opened_at") or ""),
            "already_open": False,
            "switched_from": switched_from,
            "close_result": close_result,
            "normalized_input_path": normalized_input_path,
            "resolved_open_path": resolved_open_path,
            "initialized_idb_path": initialized_idb_path,
            "cleanup_result": cleanup_result,
        }
    except Exception as e:
        _service_state["last_error"] = str(e)[:800]
        _emit_service_event(
            "db_open_failed",
            input_path=normalized_input_path,
            run_auto_analysis=bool(run_auto_analysis),
            save_current=bool(save_current),
            error=str(e)[:400],
        )
        raise


def _open_database_on_startup(input_path: str):
    """启动时可选打开数据库。"""
    if not str(input_path or "").strip():
        logger.info("No startup input configured. Use /db/open to open binary or IDB later.")
        return

    with _exec_lock:
        try:
            _open_database_locked(input_path=input_path, run_auto_analysis=True, save_current=True)
        except Exception as e:
            raise RuntimeError(f"Failed to open database on startup: {e}")


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify(
        {
            "status": "ok",
            "debug_mode": config.DEBUG_MODE,
            "has_ida": _check_ida_available(),
            "db_opened": _db_state["opened"],
            "db_path": _db_state["path"],
            "db_opened_at": _db_state["opened_at"],
            "service_status": _service_status_snapshot(),
        }
    )


@app.route("/status", methods=["GET"])
def service_status():
    return jsonify(
        {
            "status": "ok",
            "service": _service_status_snapshot(),
        }
    )


@app.route("/db/open", methods=["POST"])
def open_db():
    data = request.json or {}
    input_path = str(data.get("input_path", "") or "").strip()
    run_auto_analysis = bool(data.get("run_auto_analysis", True))
    save_current = bool(data.get("save_current", True))

    if not input_path:
        return jsonify({"success": False, "error": "Missing 'input_path' field"}), 400

    with _exec_lock:
        try:
            result = _open_database_locked(
                input_path=input_path,
                run_auto_analysis=run_auto_analysis,
                save_current=save_current,
            )
            return jsonify({"success": True, "result": result})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})


@app.route("/db/close", methods=["POST"])
def close_db():
    data = request.json or {}
    save = bool(data.get("save", True))

    with _exec_lock:
        try:
            result = _close_database_locked(save=save)
            return jsonify({"success": True, "result": result})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})


@app.route("/db/info", methods=["GET"])
def get_db_info():
    gate = _require_db_opened()
    if gate:
        return gate

    with _exec_lock:
        try:
            info = {
                "path": idc.get_idb_path(),
                "processor": idc.get_inf_attr(idc.INF_PROCNAME) if hasattr(idc, "INF_PROCNAME") else "unknown",
                "base_addr": idaapi.get_imagebase(),
                "min_ea": idc.get_inf_attr(idc.INF_MIN_EA) if hasattr(idc, "INF_MIN_EA") else 0,
                "max_ea": idc.get_inf_attr(idc.INF_MAX_EA) if hasattr(idc, "INF_MAX_EA") else 0,
            }
            return jsonify({"success": True, "result": info})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})


def _safe_name(text):
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", str(text or "").strip())
    return value.strip("_")


@app.route("/db/backup", methods=["POST"])
def backup_db():
    gate = _require_db_opened()
    if gate:
        return gate

    data = request.json or {}
    backup_dir = str(data.get("backup_dir", "") or "").strip()
    tag = str(data.get("tag", "") or "").strip()
    filename = str(data.get("filename", "") or "").strip()

    with _exec_lock:
        started = time.time()
        current_path = str(idc.get_idb_path() or "").strip()
        if not current_path:
            return jsonify({"success": False, "error": "empty current idb path"})

        requested_dir = str(backup_dir or "").strip()
        requested_tag = _safe_name(tag)
        requested_filename = str(filename or "").strip()

        if not requested_dir:
            requested_dir = os.path.join(os.path.dirname(current_path), "backups")
        os.makedirs(requested_dir, exist_ok=True)

        base_name = os.path.basename(current_path)
        stem, ext = os.path.splitext(base_name)
        if not ext:
            ext = ".i64"

        if requested_filename:
            target_path = os.path.join(requested_dir, requested_filename)
            if not os.path.splitext(target_path)[1]:
                target_path = target_path + ext
        else:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            suffix = requested_tag or "backup"
            target_path = os.path.join(requested_dir, f"{stem}_{timestamp}_{suffix}{ext}")

        save_ok = False
        save_method = ""
        save_error = ""
        try:
            saved_path = _save_current_database_as(target_path)
            save_ok = True
            save_method = "execute_script.save_database"
            target_path = str(saved_path or target_path).strip() or target_path
        except Exception as e:
            save_error = str(e)
            save_ok = False

        if not save_ok:
            try:
                shutil.copy2(current_path, target_path)
                save_ok = True
                save_method = "shutil.copy2"
            except Exception as e:
                if save_error:
                    save_error = f"{save_error}; copy2: {e}"
                else:
                    save_error = str(e)

        elapsed = time.time() - started
        result = {
            "success": bool(save_ok),
            "source_path": current_path,
            "backup_path": target_path,
            "method": save_method,
            "error": save_error if not save_ok else "",
        }
        if save_ok:
            logger.info("IDB backup completed in %.2fs: %s", elapsed, target_path)
        else:
            logger.warning("IDB backup failed in %.2fs: %s", elapsed, save_error)
        return jsonify({"success": bool(save_ok), "result": result})


@app.route("/execute", methods=["POST"])
def execute():
    gate = _require_db_opened()
    if gate:
        return gate

    data = request.json or {}
    script = data.get("script")
    context = data.get("context", {})

    if not script:
        return jsonify({"success": False, "error": "Missing 'script' field"}), 400

    preview = script[:100].replace("\n", " ")
    logger.info("Executing script: %s...", preview)

    result, elapsed = _execute_serialized(script, context)
    logger.info("Script completed in %.2fs: success=%s", elapsed, result.get("success"))
    return jsonify(result)


@app.route("/functions", methods=["GET"])
def list_functions():
    gate = _require_db_opened()
    if gate:
        return gate

    with _exec_lock:
        try:
            functions = []
            for func_ea in idautils.Functions():
                func = ida_funcs.get_func(func_ea)
                size = func.size() if func else 0
                functions.append({
                    "ea": func_ea,
                    "name": idc.get_func_name(func_ea),
                    "size": size,
                })
            logger.info("Found %d functions", len(functions))
            return jsonify({"success": True, "result": functions})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})


@app.route("/decompile", methods=["POST"])
def decompile_function():
    gate = _require_db_opened()
    if gate:
        return gate

    data = request.json or {}
    func_name = data.get("function_name")
    if not func_name:
        func_name = data.get("name")
    func_addr = data.get("ea")
    if func_addr is None:
        func_addr = data.get("addr")

    with _exec_lock:
        try:
            if func_name:
                ea = idc.get_name_ea_simple(func_name)
                if ea == idc.BADADDR:
                    return jsonify({"success": False, "error": f"Function not found: {func_name}"})
                target_ea = ea
            elif func_addr is not None:
                if isinstance(func_addr, str):
                    target_ea = int(func_addr, 0)
                else:
                    target_ea = int(func_addr)
            else:
                return jsonify(
                    {
                        "success": False,
                        "error": "Missing 'function_name'/'ea' (or legacy 'name'/'addr') parameter",
                    }
                ), 400

            cfunc = ida_hexrays.decompile(target_ea)
            if cfunc:
                lines = []
                for i, line in enumerate(cfunc.get_pseudocode(), 1):
                    lines.append(f"[{i:4d}] {ida_lines.tag_remove(line.line)}")
                code_text = "\n".join(lines)
                return jsonify({"success": True, "result": code_text})
            else:
                return jsonify({"success": False, "error": "Decompilation failed"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})


@app.route("/search", methods=["POST"])
def search():
    gate = _require_db_opened()
    if gate:
        return gate

    data = request.json or {}
    pattern = data.get("pattern", "")
    target_type = data.get("target_type", "all")
    offset = data.get("offset", 0)
    count = data.get("count", 20)
    flags = data.get("flags", "IGNORECASE")

    started = time.time()
    try:
        with _exec_lock:
            result = search_symbols_strings(
                pattern=str(pattern or ""),
                target_type=str(target_type or "all"),
                offset=int(offset),
                count=int(count),
                flags=str(flags or "IGNORECASE"),
            )
        elapsed = time.time() - started
        logger.info(
            "/search done in %.2fs pattern=%r target_type=%s returned=%d total=%d",
            elapsed,
            str(pattern or "")[:120],
            str(target_type or "all"),
            int(result.get("returned_count", 0)),
            int(result.get("total_count", 0)),
        )
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/xrefs", methods=["POST"])
def xrefs():
    gate = _require_db_opened()
    if gate:
        return gate

    data = request.json or {}
    target = data.get("target", "")
    target_type = data.get("target_type", "")
    direction = data.get("direction", "to")
    offset = data.get("offset", 0)
    count = data.get("count", 20)
    flags = data.get("flags", "IGNORECASE")

    started = time.time()
    try:
        with _exec_lock:
            result = search_xrefs(
                target=str(target or ""),
                target_type=str(target_type or ""),
                direction=str(direction or "to"),
                offset=int(offset),
                count=int(count),
                flags=str(flags or "IGNORECASE"),
            )
        elapsed = time.time() - started
        logger.info(
            "/xrefs done in %.2fs target=%r target_type=%s direction=%s returned=%d total=%d",
            elapsed,
            str(target or "")[:120],
            str(target_type or ""),
            str(direction or "to"),
            int(result.get("returned_count", 0)),
            int(result.get("total_count", 0)),
        )
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


def main():
    parser = argparse.ArgumentParser(description="IDA Service Daemon")
    parser.add_argument("--host", default=config.HOST, help="Server host")
    parser.add_argument("--port", type=int, default=config.PORT, help="Server port")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--input-path", help="Binary or IDB path to open on startup")
    parser.add_argument("--log-dir", default=config.LOG_DIR, help="Log directory")

    args = parser.parse_args()

    if args.debug:
        config.DEBUG_MODE = True
        config.LOG_LEVEL = "DEBUG"

    setup_logging(args.log_dir, args.debug)

    if config.DEBUG_MODE:
        os.makedirs(config.DEBUG_SCRIPT_DIR, exist_ok=True)

    logger.info("=" * 60)
    logger.info("IDA Service Daemon v0.2.0")
    logger.info("Host: %s", args.host)
    logger.info("Port: %s", args.port)
    logger.info("Debug Mode: %s", config.DEBUG_MODE)
    logger.info("IDA Available: %s", _check_ida_available())
    logger.info("=" * 60)

    if not _check_ida_available():
        logger.warning("Running in mock mode (IDA modules unavailable)")
    else:
        input_path = args.input_path or config.DEFAULT_INPUT_PATH
        _open_database_on_startup(str(input_path or ""))

    logger.info("Starting server...")
    app.run(
        host=args.host,
        port=args.port,
        debug=config.DEBUG_MODE,
        threaded=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
