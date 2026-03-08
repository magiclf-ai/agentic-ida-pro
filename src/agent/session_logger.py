"""Agent session logger - Simple sequential message log."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentSessionLogger:
    """Session logger with sequential message storage."""

    def __init__(self, log_dir: str):
        os.makedirs(log_dir, exist_ok=True)
        self.session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self.db_path = os.path.join(log_dir, "agent_observability.sqlite3")
        self._lock = threading.Lock()
        self._msg_index = 0
        self._db = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        cur = self._db.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA busy_timeout=5000;")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
              session_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              log_path TEXT DEFAULT '',
              binary_name TEXT DEFAULT ''
            )
            """
        )
        session_columns = {str(row["name"]) for row in cur.execute("PRAGMA table_info(sessions)").fetchall()}
        if "binary_name" not in session_columns:
            cur.execute("ALTER TABLE sessions ADD COLUMN binary_name TEXT DEFAULT ''")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS turns (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              turn_id TEXT NOT NULL,
              agent_id TEXT NOT NULL DEFAULT 'main',
              agent_name TEXT DEFAULT '',
              parent_agent_id TEXT DEFAULT '',
              iteration INTEGER DEFAULT 0,
              phase TEXT DEFAULT '',
              status TEXT NOT NULL,
              started_at TEXT,
              completed_at TEXT,
              input_tokens INTEGER,
              output_tokens INTEGER,
              latency_s REAL,
              error_text TEXT DEFAULT '',
              UNIQUE(session_id, turn_id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id)")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              turn_id TEXT,
              msg_index INTEGER NOT NULL,
              role TEXT NOT NULL,
              name TEXT,
              content TEXT,
              tool_calls TEXT,
              tool_call_id TEXT,
              latency_ms INTEGER,
              is_error INTEGER DEFAULT 0,
              created_at TEXT NOT NULL
            )
            """
        )
        message_columns = {str(row["name"]) for row in cur.execute("PRAGMA table_info(messages)").fetchall()}
        if "name" not in message_columns:
            cur.execute("ALTER TABLE messages ADD COLUMN name TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, msg_index)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_turn ON messages(session_id, turn_id)")

        # Store tools available for each turn (bound tools)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_tools (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              turn_id TEXT NOT NULL,
              tool_name TEXT NOT NULL,
              tool_description TEXT,
              tool_schema TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_turn_tools_session ON turn_tools(session_id, turn_id)")

        # Store tool calls actually executed in each turn.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS executed_tool_calls (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              turn_id TEXT NOT NULL,
              tool_call_id TEXT DEFAULT '',
              tool_name TEXT NOT NULL,
              is_error INTEGER DEFAULT 0,
              mutation_effective INTEGER,
              duration_ms INTEGER DEFAULT 0,
              result_preview TEXT DEFAULT '',
              created_at TEXT NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exec_tools_session ON executed_tool_calls(session_id, turn_id, id)")

        # Insert session
        now = _utc_now_iso()
        cur.execute(
            "INSERT OR REPLACE INTO sessions(session_id, created_at, updated_at, log_path, binary_name) VALUES(?,?,?,?,?)",
            (self.session_id, now, now, "", ""),
        )
        self._db.commit()

    def _add_message(
        self,
        turn_id: str,
        role: str,
        content: str = "",
        tool_calls: Optional[List[Dict]] = None,
        tool_call_id: str = "",
        is_error: bool = False,
        name: str = "",
    ) -> None:
        self._msg_index += 1
        now = _utc_now_iso()
        self._db.execute(
            """
            INSERT INTO messages(session_id, turn_id, msg_index, role, name, content,
                                 tool_calls, tool_call_id, is_error, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.session_id,
                turn_id,
                self._msg_index,
                role,
                name[:100] if name else None,
                content[:40000] if content else "",
                json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                tool_call_id,
                1 if is_error else 0,
                now,
            ),
        )
        self._db.execute(
            "UPDATE sessions SET updated_at=? WHERE session_id=?",
            (now, self.session_id),
        )
        self._db.commit()

    def _ensure_turn(self, turn_id: str, agent_id: str = "main", agent_name: str = "", iteration: int = 0, phase: str = "", status: str = "running") -> None:
        """Ensure turn record exists in turns table."""
        now = _utc_now_iso()
        self._db.execute(
            """
            INSERT OR IGNORE INTO turns(session_id, turn_id, agent_id, agent_name, iteration, phase, status, started_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (self.session_id, turn_id, agent_id, agent_name, iteration, phase, status, now),
        )
        self._db.commit()

    def _update_turn_complete(self, turn_id: str, status: str = "completed", input_tokens: int = None, output_tokens: int = None, latency_s: float = None, error_text: str = "") -> None:
        """Mark turn as completed with metrics."""
        now = _utc_now_iso()
        self._db.execute(
            """
            UPDATE turns SET status=?, completed_at=?, input_tokens=?, output_tokens=?, latency_s=?, error_text=?
            WHERE session_id=? AND turn_id=?
            """,
            (status, now, input_tokens, output_tokens, latency_s, error_text, self.session_id, turn_id),
        )
        self._db.commit()

    def _log_bound_tools(self, turn_id: str, tools: list) -> None:
        """Log the tools bound for a specific turn."""
        if not tools:
            return
        now = _utc_now_iso()
        for tool in tools:
            tool_name = ""
            tool_desc = ""
            tool_schema_json = ""
            if isinstance(tool, dict):
                tool_name = str(tool.get("name", "") or "")
                tool_desc = str(tool.get("description", "") or "")[:500]
                # 尝试获取 schema
                schema = tool.get("parameters") or tool.get("args_schema")
                if schema:
                    try:
                        if hasattr(schema, "model_json_schema"):
                            tool_schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
                        elif hasattr(schema, "schema"):
                            tool_schema_json = json.dumps(schema.schema(), ensure_ascii=False)
                        else:
                            tool_schema_json = json.dumps(schema, ensure_ascii=False)
                    except Exception:
                        pass
            else:
                # Handle LangChain tool objects
                tool_name = str(getattr(tool, "name", "") or "")
                tool_desc = str(getattr(tool, "description", "") or "")[:500]
                # 获取完整的 tool schema
                try:
                    if hasattr(tool, "tool_schema"):
                        schema = tool.tool_schema
                        if isinstance(schema, dict):
                            tool_schema_json = json.dumps(schema, ensure_ascii=False)
                    elif hasattr(tool, "args_schema") and tool.args_schema:
                        args_schema = tool.args_schema
                        if hasattr(args_schema, "model_json_schema"):
                            tool_schema_json = json.dumps(args_schema.model_json_schema(), ensure_ascii=False)
                        elif hasattr(args_schema, "schema"):
                            tool_schema_json = json.dumps(args_schema.schema(), ensure_ascii=False)
                except Exception:
                    pass
            if not tool_name:
                continue
            self._db.execute(
                """
                INSERT INTO turn_tools(session_id, turn_id, tool_name, tool_description, tool_schema, created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (self.session_id, turn_id, tool_name, tool_desc, tool_schema_json, now),
            )
        self._db.commit()

    def _log_executed_tool_calls(self, turn_id: str, tool_calls: list) -> None:
        """Log actually executed tool calls for a turn."""
        if not tool_calls:
            return
        now = _utc_now_iso()
        for row in tool_calls:
            if not isinstance(row, dict):
                continue
            tool_name = str(row.get("tool_name", "") or "").strip()
            if not tool_name:
                continue
            mutation_effective = row.get("mutation_effective")
            mutation_value = None
            if mutation_effective is True:
                mutation_value = 1
            elif mutation_effective is False:
                mutation_value = 0
            self._db.execute(
                """
                INSERT INTO executed_tool_calls(
                  session_id, turn_id, tool_call_id, tool_name, is_error,
                  mutation_effective, duration_ms, result_preview, created_at
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.session_id,
                    turn_id,
                    str(row.get("tool_call_id", "") or ""),
                    tool_name,
                    1 if bool(row.get("is_error", False)) else 0,
                    mutation_value,
                    int(row.get("duration_ms", 0) or 0),
                    str(row.get("result_preview", "") or "")[:2000],
                    now,
                ),
            )
        self._db.execute(
            "UPDATE sessions SET updated_at=? WHERE session_id=?",
            (now, self.session_id),
        )
        self._db.commit()

    def log(self, event: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            turn_id = payload.get("turn_id", "")
            if not turn_id:
                return

            agent_id = payload.get("agent_id", "main")
            agent_name = payload.get("agent_name", "")
            iteration = payload.get("iteration", 0)
            phase = payload.get("phase", "")

            if event == "llm_interaction":
                # Ensure turn exists
                self._ensure_turn(turn_id, agent_id, agent_name, iteration, phase, "running")

                for msg in payload.get("messages", []):
                    tool_calls = msg.get("tool_calls", [])
                    if not isinstance(tool_calls, list):
                        tool_calls = []
                    content = msg.get("content", "")
                    role = msg.get("role", "user")
                    name = msg.get("name", "")
                    self._add_message(
                        turn_id=turn_id,
                        role=role,
                        content=content,
                        tool_calls=tool_calls,
                        tool_call_id=str(msg.get("tool_call_id", "") or ""),
                        is_error=bool(msg.get("is_error", False)),
                        name=name,
                    )

                # Update turn metrics if provided
                usage = payload.get("usage", {})
                latency_s = payload.get("latency_s")
                if usage or latency_s is not None:
                    self._update_turn_complete(
                        turn_id,
                        status="completed",
                        input_tokens=usage.get("input_tokens") if isinstance(usage, dict) else None,
                        output_tokens=usage.get("output_tokens") if isinstance(usage, dict) else None,
                        latency_s=latency_s,
                    )

            elif event == "llm_response_failed":
                self._ensure_turn(turn_id, agent_id, agent_name, iteration, phase, "failed")
                self._add_message(
                    turn_id=turn_id,
                    role="assistant",
                    content=f"ERROR: {str(payload.get('error', '') or '').strip()}",
                    is_error=True,
                    name="",
                )
                self._update_turn_complete(turn_id, status="failed", error_text=str(payload.get("error", "")))

            elif event == "turn_complete":
                self._update_turn_complete(
                    turn_id,
                    status=payload.get("status", "completed"),
                    input_tokens=payload.get("input_tokens"),
                    output_tokens=payload.get("output_tokens"),
                    latency_s=payload.get("latency_s"),
                )

            elif event == "tools_bound":
                tools = payload.get("tools", [])
                self._log_bound_tools(turn_id, tools)

            elif event == "tool_batch_executed":
                tool_calls = payload.get("tool_calls", [])
                self._ensure_turn(turn_id, agent_id, agent_name, iteration, phase or "", "running")
                self._log_executed_tool_calls(turn_id, tool_calls if isinstance(tool_calls, list) else [])

    def set_binary_name(self, binary_name: str) -> None:
        """Set the binary file name for this session."""
        now = _utc_now_iso()
        self._db.execute(
            "UPDATE sessions SET binary_name=?, updated_at=? WHERE session_id=?",
            (binary_name, now, self.session_id),
        )
        self._db.commit()

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass

    def __del__(self):
        self.close()
