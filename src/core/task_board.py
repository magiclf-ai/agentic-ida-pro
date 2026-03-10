"""Task board with plain-text APIs for LLM-driven planning loops."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class TaskItem:
    task_id: str
    title: str
    status: str = "todo"
    priority: str = "normal"
    details: str = ""
    owner: str = "main"
    created_at: float = 0.0
    updated_at: float = 0.0
    last_note: str = ""


class TaskBoard:
    VALID_STATUS = {"todo", "in_progress", "blocked", "done", "cancelled"}
    VALID_VIEW = {"plan", "status", "both"}

    def __init__(self, agent_id: str = "main", on_change: Optional[Callable[[List[str]], None]] = None):
        self.agent_id = str(agent_id or "main").strip() or "main"
        self._on_change = on_change
        self._task_board: Dict[str, TaskItem] = {}
        self._task_order: List[str] = []
        self._task_seq: int = 0

    def set_on_change(self, on_change: Optional[Callable[[List[str]], None]]) -> None:
        self._on_change = on_change

    def reset(self) -> None:
        self._task_board = {}
        self._task_order = []
        self._task_seq = 0

    def task_count(self) -> int:
        return len(self._task_order)

    def _emit_change(self, changed_task_ids: List[str]) -> None:
        if not self._on_change:
            return
        try:
            self._on_change([str(x) for x in changed_task_ids if str(x or "").strip()])
        except Exception:
            return

    def _next_task_id(self) -> str:
        self._task_seq += 1
        return f"t{self._task_seq:03d}"

    @staticmethod
    def _to_checkbox(row: TaskItem) -> str:
        done = row.status in {"done", "cancelled"}
        mark = "x" if done else " "
        note = f" | note: {row.last_note}" if row.last_note else ""
        return f"- [{mark}] {row.task_id} ({row.priority}) {row.title} [status={row.status}]{note}"

    def _resolve_task_ref(self, task_ref: str) -> Tuple[Optional[TaskItem], str]:
        key = str(task_ref or "").strip()
        if not key:
            return None, "ERROR: missing task_ref"
        if key in self._task_board:
            return self._task_board[key], ""

        lower = key.lower()
        exact: List[TaskItem] = []
        fuzzy: List[TaskItem] = []
        for task_id in self._task_order:
            row = self._task_board.get(task_id)
            if not row:
                continue
            title = row.title.strip().lower()
            if title == lower:
                exact.append(row)
            elif lower in title:
                fuzzy.append(row)

        matches = exact if exact else fuzzy
        if not matches:
            return None, f"ERROR: task not found: {task_ref}"
        if len(matches) > 1:
            ids = ", ".join([row.task_id for row in matches[:6]])
            return None, f"ERROR: task ref is ambiguous: {task_ref} (matches: {ids})"
        return matches[0], ""

    def create_task(self, title: str, details: str = "", priority: str = "normal", owner: str = "main") -> str:
        text = str(title or "").strip()
        if not text:
            return "ERROR: missing title"
        now = time.time()
        task_id = self._next_task_id()
        row = TaskItem(
            task_id=task_id,
            title=text,
            status="todo",
            priority=str(priority or "normal").strip().lower() or "normal",
            details=str(details or "").strip(),
            owner=str(owner or "main").strip() or "main",
            created_at=now,
            updated_at=now,
        )
        self._task_board[task_id] = row
        self._task_order.append(task_id)
        self._emit_change([task_id])
        return f"OK: created task {task_id}\n{self._to_checkbox(row)}"

    def create_tasks(self, tasks: List[Dict[str, Any]], owner: str = "main") -> str:
        if not isinstance(tasks, list) or (not tasks):
            return "ERROR: missing tasks"

        normalized: List[Dict[str, str]] = []
        for idx, raw in enumerate(tasks, start=1):
            if not isinstance(raw, dict):
                return f"ERROR: tasks[{idx}] must be an object"
            title = str(raw.get("title", "") or "").strip()
            if not title:
                return f"ERROR: tasks[{idx}].title is required"
            normalized.append(
                {
                    "title": title,
                    "details": str(raw.get("details", "") or "").strip(),
                    "priority": str(raw.get("priority", "normal") or "").strip().lower() or "normal",
                }
            )

        now = time.time()
        created_rows: List[TaskItem] = []
        changed_task_ids: List[str] = []
        task_owner = str(owner or "main").strip() or "main"
        for item in normalized:
            task_id = self._next_task_id()
            row = TaskItem(
                task_id=task_id,
                title=item["title"],
                status="todo",
                priority=item["priority"],
                details=item["details"],
                owner=task_owner,
                created_at=now,
                updated_at=now,
            )
            self._task_board[task_id] = row
            self._task_order.append(task_id)
            created_rows.append(row)
            changed_task_ids.append(task_id)

        self._emit_change(changed_task_ids)
        return "OK: created {count} tasks\n{rows}".format(
            count=len(created_rows),
            rows="\n".join([self._to_checkbox(row) for row in created_rows]),
        )

    def set_task_status(self, task_ref: str, status: str, note: str = "", owner: str = "") -> str:
        row, err = self._resolve_task_ref(task_ref)
        if err:
            return err
        assert row is not None

        picked_status = str(status or "").strip().lower()
        if not picked_status:
            return "ERROR: missing status"
        if picked_status not in self.VALID_STATUS:
            return f"ERROR: invalid status '{picked_status}'"

        row.status = picked_status
        if str(note or "").strip():
            row.last_note = str(note or "").strip()
        if str(owner or "").strip():
            row.owner = str(owner or "").strip()
        row.updated_at = time.time()
        self._emit_change([row.task_id])
        return f"OK: updated task {row.task_id}\n{self._to_checkbox(row)}"

    def edit_task(
        self,
        task_ref: str,
        title: str = "",
        details: str = "",
        priority: str = "",
        owner: str = "",
        note: str = "",
    ) -> str:
        row, err = self._resolve_task_ref(task_ref)
        if err:
            return err
        assert row is not None

        touched = False
        if title != "":
            new_title = str(title or "").strip()
            if not new_title:
                return "ERROR: title cannot be empty"
            row.title = new_title
            touched = True
        if details != "":
            row.details = str(details or "").strip()
            touched = True
        if priority != "":
            row.priority = str(priority or "").strip().lower() or row.priority
            touched = True
        if owner != "":
            row.owner = str(owner or "").strip() or row.owner
            touched = True
        if note != "":
            row.last_note = str(note or "").strip()
            touched = True
        if not touched:
            return "ERROR: no editable fields provided"

        row.updated_at = time.time()
        self._emit_change([row.task_id])
        return f"OK: edited task {row.task_id}\n{self._to_checkbox(row)}"

    def _render_rows(self, *, include_done: bool, filter_status: str = "") -> str:
        picked_status = str(filter_status or "").strip().lower()
        if picked_status and picked_status not in self.VALID_STATUS:
            return f"ERROR: invalid filter_status '{picked_status}'"

        rows: List[str] = []
        for task_id in self._task_order:
            row = self._task_board.get(task_id)
            if not row:
                continue
            if picked_status and row.status != picked_status:
                continue
            if (not include_done) and row.status in {"done", "cancelled"}:
                continue
            rows.append(self._to_checkbox(row))
        return "\n".join(rows) if rows else "(empty)"

    def render_plan_board(self, filter_status: str = "") -> str:
        return self._render_rows(include_done=True, filter_status=filter_status)

    def render_status_board(self, filter_status: str = "") -> str:
        rows_text = self._render_rows(include_done=False, filter_status=filter_status)
        if rows_text.startswith("ERROR:"):
            return rows_text
        total = len(self._task_order)
        done = 0
        blocked = 0
        for task_id in self._task_order:
            row = self._task_board.get(task_id)
            if not row:
                continue
            if row.status in {"done", "cancelled"}:
                done += 1
            if row.status == "blocked":
                blocked += 1
        active = max(0, total - done)
        return (
            f"- summary: total={total}, active={active}, done={done}, blocked={blocked}\n"
            f"{rows_text}"
        )

    def get_task_board(self, view: str = "both", filter_status: str = "") -> str:
        picked_view = str(view or "both").strip().lower() or "both"
        if picked_view not in self.VALID_VIEW:
            return f"ERROR: invalid view '{picked_view}'"
        if str(filter_status or "").strip().lower() and str(filter_status or "").strip().lower() not in self.VALID_STATUS:
            return f"ERROR: invalid filter_status '{str(filter_status).strip().lower()}'"

        if picked_view == "plan":
            return "## Task Plan (Full)\n" + self.render_plan_board(filter_status=filter_status)
        if picked_view == "status":
            return "## Task Status (Current)\n" + self.render_status_board(filter_status=filter_status)
        return (
            "## Task Plan (Full)\n"
            + self.render_plan_board(filter_status=filter_status)
            + "\n\n## Task Status (Current)\n"
            + self.render_status_board(filter_status=filter_status)
        )
