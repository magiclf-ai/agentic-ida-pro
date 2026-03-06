#!/usr/bin/env python3
"""Serve observability API (Flask + SQLite)."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, request


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _fetch_sessions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            s.session_id,
            s.created_at,
            s.updated_at,
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
        """
    ).fetchall()
    return [
        {
            "session_id": str(row["session_id"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "turn_count": int(row["turn_count"] or 0),
            "message_count": int(row["message_count"] or 0),
            "goal": str(row["goal"] or ""),
            "status": str(row["status"] or "pending"),
        }
        for row in rows
    ]


def _fetch_turns(conn: sqlite3.Connection, session_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            id, turn_id, agent_id, agent_name, parent_agent_id,
            iteration, phase, status,
            started_at, completed_at,
            input_tokens, output_tokens, latency_s, error_text
        FROM turns
        WHERE session_id = ?
        ORDER BY id ASC
        """,
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
            id, turn_id, tool_name, tool_description, created_at
        FROM turn_tools
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (session_id,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "turn_id": str(row["turn_id"] or ""),
            "tool_name": str(row["tool_name"] or ""),
            "tool_description": str(row["tool_description"] or ""),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


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
