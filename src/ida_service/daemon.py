#!/usr/bin/env python3
"""
IDA Service Daemon
- 启动时打开数据库
- 串行执行所有 IDA 脚本（主线程约束）
"""
import argparse
import logging
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

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
_db_state: Dict[str, Optional[str]] = {
    "opened": False,
    "path": None,
    "opened_at": None,
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
        result = execute_script(code, context)
        elapsed = time.time() - started
        if elapsed > config.SCRIPT_TIMEOUT:
            logger.warning(
                "Script exceeded configured timeout: %.2fs > %ss",
                elapsed,
                config.SCRIPT_TIMEOUT,
            )
        return result, elapsed


def _require_db_opened():
    if not _db_state["opened"]:
        return (
            jsonify({"success": False, "error": "Database not opened. Restart service with --idb."}),
            503,
        )
    return None


def _open_database_on_startup(db_path: str):
    """启动时打开数据库，失败则抛异常。"""
    if not db_path:
        raise RuntimeError("Missing IDB path. Please pass --idb or set IDA_DEFAULT_IDB_PATH.")

    if not os.path.exists(db_path):
        raise RuntimeError(f"IDB file not found: {db_path}")

    with _exec_lock:
        started = time.time()
        try:
            idapro.open_database(db_path, True)
            _db_state["opened"] = True
            _db_state["path"] = idc.get_idb_path() or db_path
            _db_state["opened_at"] = datetime.now(timezone.utc).isoformat()
            elapsed = time.time() - started
            logger.info("Database opened in %.2fs: %s", elapsed, _db_state["path"])
        except Exception as e:
            raise RuntimeError(f"Failed to open database: {e}")


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify(
        {
            "status": "ok",
            "debug_mode": config.DEBUG_MODE,
            "has_ida": _check_ida_available(),
            "db_opened": _db_state["opened"],
            "db_path": _db_state["path"],
        }
    )


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
            save_ok = bool(idc.save_database(target_path, 0))
            if save_ok:
                save_method = "idc.save_database"
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
    parser.add_argument("--idb", help="IDB path to open on startup")
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
        idb_path = args.idb or config.DEFAULT_IDB_PATH
        _open_database_on_startup(idb_path)

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
