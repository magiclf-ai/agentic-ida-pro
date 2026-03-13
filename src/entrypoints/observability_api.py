#!/usr/bin/env python3
"""Serve observability API (Flask + SQLite)."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, request


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (str(table_name or "").strip(),),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _has_table(conn, table_name):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return str(column_name or "").strip() in _table_columns(conn, table_name)


def _parse_payload_text(payload_text: Any) -> Dict[str, Any]:
    raw = str(payload_text or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {"text": raw}
    return data if isinstance(data, dict) else {"value": data}


def _seconds_since(iso_text: str) -> float:
    value = str(iso_text or "").strip()
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _fetch_sessions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    has_binary_name = _has_column(conn, "sessions", "binary_name")

    sql = """
        SELECT
            s.session_id,
            s.created_at,
            s.updated_at,
            {binary_col}
            COUNT(DISTINCT t.id) as turn_count,
            COUNT(DISTINCT m.id) as message_count,
            (SELECT content FROM messages
             WHERE session_id = s.session_id AND role = 'user'
             ORDER BY msg_index ASC LIMIT 1) as goal,
            COALESCE(
                MAX(CASE WHEN t.status = 'running' THEN 'running' END),
                MAX(CASE WHEN t.status = 'failed' THEN 'failed' END),
                MAX(CASE WHEN t.status = 'completed' THEN 'completed' END),
                'pending'
            ) as status
        FROM sessions s
        LEFT JOIN turns t ON t.session_id = s.session_id
        LEFT JOIN messages m ON m.session_id = s.session_id
        GROUP BY s.session_id
        ORDER BY s.updated_at DESC
    """.format(binary_col="s.binary_name," if has_binary_name else "'' as binary_name,")

    rows = conn.execute(sql).fetchall()
    return [
        {
            "session_id": str(row["session_id"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "binary_name": str(row["binary_name"] or ""),
            "turn_count": int(row["turn_count"] or 0),
            "message_count": int(row["message_count"] or 0),
            "goal": str(row["goal"] or ""),
            "status": str(row["status"] or "pending"),
        }
        for row in rows
    ]


def _fetch_turns(conn: sqlite3.Connection, session_id: str) -> List[Dict[str, Any]]:
    has_parent_agent_id = _has_column(conn, "turns", "parent_agent_id")
    rows = conn.execute(
        """
        SELECT
            id, turn_id, agent_id, agent_name, {parent_agent_col},
            iteration, phase, status,
            started_at, completed_at,
            input_tokens, output_tokens, latency_s, error_text
        FROM turns
        WHERE session_id = ?
        ORDER BY id ASC
        """.format(parent_agent_col="parent_agent_id" if has_parent_agent_id else "'' AS parent_agent_id"),
        (session_id,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "turn_id": str(row["turn_id"]),
            "agent_id": str(row["agent_id"] or ""),
            "agent_name": str(row["agent_name"] or ""),
            "parent_agent_id": str(row["parent_agent_id"] or ""),
            "iteration": int(row["iteration"] or 0),
            "phase": str(row["phase"] or ""),
            "status": str(row["status"] or ""),
            "started_at": str(row["started_at"] or ""),
            "completed_at": str(row["completed_at"] or ""),
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "latency_s": row["latency_s"],
            "error_text": str(row["error_text"] or ""),
        }
        for row in rows
    ]


def _fetch_messages(conn: sqlite3.Connection, session_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT 
            id, turn_id, msg_index, role, name, content,
            tool_calls, tool_call_id, latency_ms, is_error, created_at
        FROM messages
        WHERE session_id = ?
        ORDER BY msg_index ASC
        """,
        (session_id,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "turn_id": str(row["turn_id"] or ""),
            "msg_index": int(row["msg_index"]),
            "role": str(row["role"]),
            "name": str(row["name"] or ""),
            "content": str(row["content"] or ""),
            "tool_calls": json.loads(row["tool_calls"]) if row["tool_calls"] else [],
            "tool_call_id": str(row["tool_call_id"] or ""),
            "latency_ms": row["latency_ms"],
            "is_error": bool(row["is_error"]),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


def _fetch_turn_tools(conn: sqlite3.Connection, session_id: str) -> List[Dict[str, Any]]:
    """Fetch bound tools for each turn."""
    rows = conn.execute(
        """
        SELECT 
            id, turn_id, tool_name, tool_description, tool_schema, created_at
        FROM turn_tools
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (session_id,),
    ).fetchall()
    result = []
    for row in rows:
        tool_schema_str = str(row["tool_schema"] or "")
        tool_schema = None
        if tool_schema_str:
            try:
                tool_schema = json.loads(tool_schema_str)
            except json.JSONDecodeError:
                tool_schema = tool_schema_str
        result.append({
            "id": int(row["id"]),
            "turn_id": str(row["turn_id"] or ""),
            "tool_name": str(row["tool_name"] or ""),
            "tool_description": str(row["tool_description"] or ""),
            "tool_schema": tool_schema,
            "created_at": str(row["created_at"]),
        })
    return result


def _fetch_executed_tool_calls(conn: sqlite3.Connection, session_id: str) -> List[Dict[str, Any]]:
    """Fetch tool calls actually executed for each turn."""
    try:
        rows = conn.execute(
            """
            SELECT
                id, turn_id, tool_call_id, tool_name, is_error,
                mutation_effective, duration_ms, result_preview, created_at
            FROM executed_tool_calls
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return []
        raise
    return [
        {
            "id": int(row["id"]),
            "turn_id": str(row["turn_id"] or ""),
            "tool_call_id": str(row["tool_call_id"] or ""),
            "tool_name": str(row["tool_name"] or ""),
            "is_error": bool(row["is_error"]),
            "mutation_effective": None
            if row["mutation_effective"] is None
            else bool(row["mutation_effective"]),
            "duration_ms": int(row["duration_ms"] or 0),
            "result_preview": str(row["result_preview"] or ""),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]


def _fetch_events(conn: sqlite3.Connection, session_id: str, limit: int = 100, after_seq: int = 0) -> List[Dict[str, Any]]:
    if not _has_table(conn, "session_events"):
        return []
    rows = conn.execute(
        """
        SELECT id, seq, event, payload_text, created_at
        FROM session_events
        WHERE session_id = ? AND seq > ?
        ORDER BY seq DESC
        LIMIT ?
        """,
        (session_id, max(0, int(after_seq or 0)), max(1, min(int(limit or 100), 500))),
    ).fetchall()
    ordered = list(reversed(rows))
    return [
        {
            "id": int(row["id"]),
            "seq": int(row["seq"]),
            "event": str(row["event"] or ""),
            "payload_text": str(row["payload_text"] or ""),
            "payload": _parse_payload_text(row["payload_text"]),
            "created_at": str(row["created_at"] or ""),
        }
        for row in ordered
    ]


def _fetch_session_summary(conn: sqlite3.Connection, session_id: str) -> Dict[str, Any]:
    has_log_path = _has_column(conn, "sessions", "log_path")
    has_binary_name = _has_column(conn, "sessions", "binary_name")
    session_row = conn.execute(
        """
        SELECT session_id, created_at, updated_at, {log_path_col}, {binary_name_col}
        FROM sessions
        WHERE session_id = ?
        LIMIT 1
        """.format(
            log_path_col="log_path" if has_log_path else "'' AS log_path",
            binary_name_col="binary_name" if has_binary_name else "'' AS binary_name",
        ),
        (session_id,),
    ).fetchone()
    if session_row is None:
        return {}

    turns = _fetch_turns(conn, session_id)
    events = _fetch_events(conn, session_id, limit=400, after_seq=0)
    event_total = 0
    if _has_table(conn, "session_events"):
        event_total = int(
            conn.execute("SELECT COUNT(*) FROM session_events WHERE session_id=?", (session_id,)).fetchone()[0]
        )
    executed = _fetch_executed_tool_calls(conn, session_id)

    goal_row = conn.execute(
        """
        SELECT content
        FROM messages
        WHERE session_id=? AND role='user'
        ORDER BY msg_index ASC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    goal = str(goal_row["content"] or "") if goal_row is not None else ""

    status = "pending"
    stop_reason = ""
    latest_progress = ""
    latest_event = ""
    latest_event_payload: Dict[str, Any] = {}
    latest_turn_summary: Dict[str, Any] = {}
    latest_tool_batch: Dict[str, Any] = {}
    event_counts: Dict[str, int] = {}

    for event in events:
        name = str(event.get("event", "") or "")
        event_counts[name] = int(event_counts.get(name, 0)) + 1
        payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
        latest_event = name or latest_event
        latest_event_payload = payload or latest_event_payload
        created_at = str(event.get("created_at") or "")
        if name in {"turn_started", "turn_completed", "llm_response_received", "tool_batch_executed", "idb_mutation_action"}:
            latest_progress = created_at or latest_progress
        if name == "turn_completed":
            latest_turn_summary = {
                "turn_id": str(payload.get("turn_id", "") or ""),
                "iteration": int(payload.get("iteration", 0) or 0),
                "status": str(payload.get("status", "") or ""),
                "phase": str(payload.get("phase", "") or ""),
                "agent_id": str(payload.get("agent_id", "") or ""),
                "agent_name": str(payload.get("agent_name", "") or ""),
                "created_at": created_at,
            }
        elif name == "tool_batch_executed":
            latest_tool_batch = {
                "turn_id": str(payload.get("turn_id", "") or ""),
                "recent_tools": list(payload.get("tool_calls", []) or [])[:8],
                "error_count": int(payload.get("error_count", 0) or 0),
                "duration_ms": int(payload.get("elapsed_ms", 0) or 0),
                "created_at": created_at,
            }
        elif name == "run_finished":
            status = str(payload.get("status", "completed") or "completed")
            stop_reason = str(payload.get("completed_reason", "") or "")
        elif name == "run_stopped":
            status = str(payload.get("status", "stopped") or "stopped")
            stop_reason = str(payload.get("reason", "") or "")

    if status == "pending" and turns:
        latest_turn = turns[-1]
        status = str(latest_turn.get("status", "") or "running")
        latest_turn_summary = latest_turn_summary or latest_turn
    if status == "pending" and events:
        status = "running"
    if not latest_progress:
        latest_progress = str(session_row["updated_at"] or "")

    mutation_rows = [row for row in events if str(row.get("event", "")) == "idb_mutation_action"]
    effective_mutations = [row for row in mutation_rows if row.get("payload", {}).get("mutation_effective") is True]
    recent_effective_tools = []
    for row in effective_mutations[-6:]:
        tool_name = str(row.get("payload", {}).get("tool_name", "") or "")
        if tool_name:
            recent_effective_tools.append(tool_name)

    tail_turns = turns[-6:]
    no_mutation_turns = 0
    for turn in reversed(tail_turns):
        turn_id = str(turn.get("turn_id", "") or "")
        has_effective = any(
            str(row.get("payload", {}).get("turn_id", "") or "") == turn_id
            and row.get("payload", {}).get("mutation_effective") is True
            for row in mutation_rows
        )
        if has_effective:
            break
        no_mutation_turns += 1

    stalled_seconds = _seconds_since(latest_progress)
    summary = {
        "session_id": str(session_row["session_id"]),
        "created_at": str(session_row["created_at"] or ""),
        "updated_at": str(session_row["updated_at"] or ""),
        "log_path": str(session_row["log_path"] or ""),
        "binary_name": str(session_row["binary_name"] or ""),
        "goal": goal,
        "status": status,
        "stop_reason": stop_reason,
        "turn_count": len(turns),
        "message_count": conn.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0],
        "event_count": event_total,
        "latest_event": latest_event,
        "latest_event_payload": latest_event_payload,
        "latest_progress_at": latest_progress,
        "stalled_seconds": stalled_seconds,
        "stalled": stalled_seconds >= 180.0 and status == "running",
        "latest_turn": latest_turn_summary,
        "latest_tool_batch": latest_tool_batch,
        "event_counts": event_counts,
        "mutation_status": {
            "attempt_count": len(mutation_rows),
            "effective_mutation_count": len(effective_mutations),
            "recent_effective_tools": recent_effective_tools,
            "no_mutation_turns": no_mutation_turns,
        },
        "executed_tool_call_count": len(executed),
    }
    return summary


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)
    app.config["OBS_DB_PATH"] = str(db_path)

    @app.after_request
    def _cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        return resp

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"ok": True})

    @app.route("/api/sessions", methods=["GET"])
    def sessions():
        try:
            with _connect(app.config["OBS_DB_PATH"]) as conn:
                rows = _fetch_sessions(conn)
            return jsonify({"sessions": rows})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/messages", methods=["GET"])
    def messages():
        session_id = str(request.args.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "missing session_id"}), 400
        try:
            with _connect(app.config["OBS_DB_PATH"]) as conn:
                rows = _fetch_messages(conn, session_id)
            return jsonify({"session_id": session_id, "messages": rows})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/turns", methods=["GET"])
    def turns():
        session_id = str(request.args.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "missing session_id"}), 400
        try:
            with _connect(app.config["OBS_DB_PATH"]) as conn:
                rows = _fetch_turns(conn, session_id)
            return jsonify({"session_id": session_id, "turns": rows})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/turn_tools", methods=["GET"])
    def turn_tools():
        session_id = str(request.args.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "missing session_id"}), 400
        try:
            with _connect(app.config["OBS_DB_PATH"]) as conn:
                rows = _fetch_turn_tools(conn, session_id)
            return jsonify({"session_id": session_id, "turn_tools": rows})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/executed_tool_calls", methods=["GET"])
    def executed_tool_calls():
        session_id = str(request.args.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "missing session_id"}), 400
        try:
            with _connect(app.config["OBS_DB_PATH"]) as conn:
                rows = _fetch_executed_tool_calls(conn, session_id)
            return jsonify({"session_id": session_id, "executed_tool_calls": rows})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/tools", methods=["GET"])
    def tools_alias():
        session_id = str(request.args.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "missing session_id"}), 400
        try:
            with _connect(app.config["OBS_DB_PATH"]) as conn:
                rows = _fetch_executed_tool_calls(conn, session_id)
            return jsonify({"session_id": session_id, "tools": rows})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/events", methods=["GET"])
    def events():
        session_id = str(request.args.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"error": "missing session_id"}), 400
        limit = int(request.args.get("limit", 100) or 100)
        after_seq = int(request.args.get("after_seq", 0) or 0)
        try:
            with _connect(app.config["OBS_DB_PATH"]) as conn:
                rows = _fetch_events(conn, session_id, limit=limit, after_seq=after_seq)
            return jsonify({"session_id": session_id, "events": rows})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sessions/<session_id>/summary", methods=["GET"])
    def session_summary(session_id: str):
        session_id = str(session_id or "").strip()
        if not session_id:
            return jsonify({"error": "missing session_id"}), 400
        try:
            with _connect(app.config["OBS_DB_PATH"]) as conn:
                summary = _fetch_session_summary(conn, session_id)
            if not summary:
                return jsonify({"error": "session not found"}), 404
            return jsonify(summary)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


def serve_observability_api(*, db_path: str, host: str, port: int) -> int:
    db_file = Path(db_path).resolve()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(str(db_file))
    print(f"[OK] observability API: http://{host}:{int(port)}")
    print(f"[INFO] db: {db_file}")
    app.run(host=host, port=int(port), threaded=True)
    return 0


def main() -> int:
    root = Path(__file__).resolve().parent.parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(root / "logs" / "agent_sessions" / "agent_observability.sqlite3"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    return serve_observability_api(db_path=args.db_path, host=args.host, port=int(args.port))


if __name__ == "__main__":
    raise SystemExit(main())
