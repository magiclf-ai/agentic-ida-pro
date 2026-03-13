"""Agent session logger with turns, messages, tools, and session events."""
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
    """Persist one agent session into SQLite for observability and watch tooling."""

    EVENT_ALIASES = {
        "session_start": "session_started",
        "session_complete": "run_finished",
        "session_incomplete": "run_finished",
        "turn_complete": "turn_completed",
    }

    def __init__(self, log_dir: str):
        os.makedirs(log_dir, exist_ok=True)
        self.session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self.db_path = os.path.join(log_dir, "agent_observability.sqlite3")
        self._lock = threading.Lock()
        self._msg_index = 0
        self._event_seq = 0
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
        if "log_path" not in session_columns:
            cur.execute("ALTER TABLE sessions ADD COLUMN log_path TEXT DEFAULT ''")

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
        turn_columns = {str(row["name"]) for row in cur.execute("PRAGMA table_info(turns)").fetchall()}
        turn_column_defs = {
            "agent_name": "TEXT DEFAULT ''",
            "parent_agent_id": "TEXT DEFAULT ''",
            "iteration": "INTEGER DEFAULT 0",
            "phase": "TEXT DEFAULT ''",
            "completed_at": "TEXT",
            "input_tokens": "INTEGER",
            "output_tokens": "INTEGER",
            "latency_s": "REAL",
            "error_text": "TEXT DEFAULT ''",
        }
        for column_name, column_def in turn_column_defs.items():
            if column_name not in turn_columns:
                cur.execute(f"ALTER TABLE turns ADD COLUMN {column_name} {column_def}")
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

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS session_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              seq INTEGER NOT NULL,
              event TEXT NOT NULL,
              payload_text TEXT DEFAULT '',
              created_at TEXT NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id, seq)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_session_events_event ON session_events(session_id, event, id)")

        row = cur.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM session_events WHERE session_id=?",
            (self.session_id,),
        ).fetchone()
        self._event_seq = int((row["max_seq"] if row else 0) or 0)

        now = _utc_now_iso()
        cur.execute(
            """
            INSERT OR REPLACE INTO sessions(session_id, created_at, updated_at, log_path, binary_name)
            VALUES(?,?,?,?,?)
            """,
            (self.session_id, now, now, "", ""),
        )
        self._db.commit()

    def _touch_session(self, now: Optional[str] = None) -> None:
        touched_at = str(now or _utc_now_iso())
        self._db.execute(
            "UPDATE sessions SET updated_at=? WHERE session_id=?",
            (touched_at, self.session_id),
        )

    def _add_message(
        self,
        turn_id: str,
        role: str,
        content: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: str = "",
        is_error: bool = False,
        name: str = "",
    ) -> None:
        self._msg_index += 1
        now = _utc_now_iso()
        self._db.execute(
            """
            INSERT INTO messages(
              session_id, turn_id, msg_index, role, name, content,
              tool_calls, tool_call_id, is_error, created_at
            )
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
        self._touch_session(now)

    def _ensure_turn(
        self,
        turn_id: str,
        agent_id: str = "main",
        agent_name: str = "",
        parent_agent_id: str = "",
        iteration: int = 0,
        phase: str = "",
        status: str = "running",
    ) -> None:
        if not str(turn_id or "").strip():
            return
        now = _utc_now_iso()
        self._db.execute(
            """
            INSERT OR IGNORE INTO turns(
              session_id, turn_id, agent_id, agent_name, parent_agent_id, iteration, phase, status, started_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                self.session_id,
                turn_id,
                agent_id,
                agent_name,
                parent_agent_id,
                int(iteration or 0),
                phase,
                status or "running",
                now,
            ),
        )
        self._db.execute(
            """
            UPDATE turns
            SET agent_id=?,
                agent_name=?,
                parent_agent_id=?,
                iteration=?,
                phase=?,
                status=COALESCE(NULLIF(?, ''), status),
                started_at=COALESCE(started_at, ?)
            WHERE session_id=? AND turn_id=?
            """,
            (
                agent_id,
                agent_name,
                parent_agent_id,
                int(iteration or 0),
                phase,
                status or "",
                now,
                self.session_id,
                turn_id,
            ),
        )
        self._touch_session(now)

    def _update_turn_complete(
        self,
        turn_id: str,
        status: str = "completed",
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_s: Optional[float] = None,
        error_text: str = "",
    ) -> None:
        if not str(turn_id or "").strip():
            return
        now = _utc_now_iso()
        self._db.execute(
            """
            UPDATE turns
            SET status=?,
                completed_at=?,
                input_tokens=COALESCE(?, input_tokens),
                output_tokens=COALESCE(?, output_tokens),
                latency_s=COALESCE(?, latency_s),
                error_text=?
            WHERE session_id=? AND turn_id=?
            """,
            (
                status or "completed",
                now,
                input_tokens,
                output_tokens,
                latency_s,
                error_text,
                self.session_id,
                turn_id,
            ),
        )
        self._touch_session(now)

    def _log_bound_tools(self, turn_id: str, tools: List[Any]) -> None:
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
                        tool_schema_json = ""
            else:
                tool_name = str(getattr(tool, "name", "") or "")
                tool_desc = str(getattr(tool, "description", "") or "")[:500]
                try:
                    if hasattr(tool, "tool_schema"):
                        schema = getattr(tool, "tool_schema")
                        if isinstance(schema, dict):
                            tool_schema_json = json.dumps(schema, ensure_ascii=False)
                    elif hasattr(tool, "args_schema") and getattr(tool, "args_schema"):
                        schema = getattr(tool, "args_schema")
                        if hasattr(schema, "model_json_schema"):
                            tool_schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
                        elif hasattr(schema, "schema"):
                            tool_schema_json = json.dumps(schema.schema(), ensure_ascii=False)
                except Exception:
                    tool_schema_json = ""
            if not tool_name:
                continue
            self._db.execute(
                """
                INSERT INTO turn_tools(session_id, turn_id, tool_name, tool_description, tool_schema, created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (self.session_id, turn_id, tool_name, tool_desc, tool_schema_json, now),
            )
        self._touch_session(now)

    def _log_executed_tool_calls(self, turn_id: str, tool_calls: List[Dict[str, Any]]) -> None:
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
        self._touch_session(now)

    @staticmethod
    def _compact_payload(event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        compact = dict(payload)
        if "messages" in compact:
            compact["message_count"] = len(compact.get("messages") or [])
            compact.pop("messages", None)
        if event == "tool_batch_executed":
            tool_calls = compact.get("tool_calls")
            if isinstance(tool_calls, list):
                compact["tool_names"] = [
                    str(row.get("tool_name", "") or "")
                    for row in tool_calls
                    if isinstance(row, dict) and str(row.get("tool_name", "") or "").strip()
                ]
        return compact

    @staticmethod
    def _payload_text(payload: Dict[str, Any]) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return str(payload or "")

    def _record_event(self, event: str, payload: Dict[str, Any]) -> None:
        now = _utc_now_iso()
        self._event_seq += 1
        self._db.execute(
            """
            INSERT INTO session_events(session_id, seq, event, payload_text, created_at)
            VALUES(?,?,?,?,?)
            """,
            (
                self.session_id,
                int(self._event_seq),
                str(event or "").strip(),
                self._payload_text(self._compact_payload(event, payload))[:12000],
                now,
            ),
        )
        self._touch_session(now)

    def log(self, event: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            event_name = str(event or "").strip()
            if not event_name:
                return
            payload = payload if isinstance(payload, dict) else {}

            recorded_event = self.EVENT_ALIASES.get(event_name, event_name)
            self._record_event(recorded_event, payload)

            turn_id = str(payload.get("turn_id", "") or "").strip()
            agent_id = str(payload.get("agent_id", "main") or "main")
            agent_name = str(payload.get("agent_name", "") or "")
            parent_agent_id = str(payload.get("parent_agent_id", "") or "")
            iteration = int(payload.get("iteration", 0) or 0)
            phase = str(payload.get("phase", "") or "")

            if not turn_id:
                self._db.commit()
                return

            if event_name == "turn_started":
                self._ensure_turn(
                    turn_id,
                    agent_id,
                    agent_name,
                    parent_agent_id,
                    iteration,
                    phase,
                    str(payload.get("status", "running") or "running"),
                )

            elif event_name in {"llm_interaction", "llm_response_received"}:
                self._ensure_turn(turn_id, agent_id, agent_name, parent_agent_id, iteration, phase, "running")
                for msg in payload.get("messages", []) or []:
                    if not isinstance(msg, dict):
                        continue
                    tool_calls = msg.get("tool_calls", [])
                    if not isinstance(tool_calls, list):
                        tool_calls = []
                    self._add_message(
                        turn_id=turn_id,
                        role=str(msg.get("role", "user") or "user"),
                        content=str(msg.get("content", "") or ""),
                        tool_calls=tool_calls,
                        tool_call_id=str(msg.get("tool_call_id", "") or ""),
                        is_error=bool(msg.get("is_error", False)),
                        name=str(msg.get("name", "") or ""),
                    )

                usage = payload.get("usage", {})
                latency_s = payload.get("latency_s")
                if isinstance(usage, dict) or latency_s is not None:
                    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") if isinstance(usage, dict) else None
                    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") if isinstance(usage, dict) else None
                    self._update_turn_complete(
                        turn_id,
                        status="completed",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_s=latency_s,
                    )

            elif event_name == "llm_response_failed":
                self._ensure_turn(turn_id, agent_id, agent_name, parent_agent_id, iteration, phase, "failed")
                self._add_message(
                    turn_id=turn_id,
                    role="assistant",
                    content=f"ERROR: {str(payload.get('error', '') or '').strip()}",
                    is_error=True,
                )
                self._update_turn_complete(turn_id, status="failed", error_text=str(payload.get("error", "") or ""))

            elif event_name in {"turn_complete", "turn_completed"}:
                self._ensure_turn(turn_id, agent_id, agent_name, parent_agent_id, iteration, phase, str(payload.get("status", "completed") or "completed"))
                self._update_turn_complete(
                    turn_id,
                    status=str(payload.get("status", "completed") or "completed"),
                    input_tokens=payload.get("input_tokens"),
                    output_tokens=payload.get("output_tokens"),
                    latency_s=payload.get("latency_s"),
                    error_text=str(payload.get("error", "") or ""),
                )

            elif event_name == "tools_bound":
                self._ensure_turn(turn_id, agent_id, agent_name, parent_agent_id, iteration, phase, "running")
                tools = payload.get("tools", [])
                self._log_bound_tools(turn_id, tools if isinstance(tools, list) else [])

            elif event_name == "tool_batch_executed":
                self._ensure_turn(turn_id, agent_id, agent_name, parent_agent_id, iteration, phase or "", "running")
                tool_calls = payload.get("tool_calls", [])
                self._log_executed_tool_calls(turn_id, tool_calls if isinstance(tool_calls, list) else [])

            self._db.commit()

    def set_binary_name(self, binary_name: str) -> None:
        now = _utc_now_iso()
        self._db.execute(
            "UPDATE sessions SET binary_name=?, updated_at=? WHERE session_id=?",
            (str(binary_name or ""), now, self.session_id),
        )
        self._db.commit()

    def set_log_path(self, log_path: str) -> None:
        now = _utc_now_iso()
        self._db.execute(
            "UPDATE sessions SET log_path=?, updated_at=? WHERE session_id=?",
            (str(log_path or ""), now, self.session_id),
        )
        self._db.commit()

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()
