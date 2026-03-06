#!/usr/bin/env python3
"""Run reverse expert agent and emit before/after recovery report."""
import argparse
import json
import os
import re
import signal
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.expert_core import ReverseExpertAgentCoreSync


REQUIRED_MODEL = "gpt-5.2"


def _snapshot_structs(agent: ReverseExpertAgentCoreSync) -> Dict[str, Any]:
    script = r'''
import idautils
import idc
import traceback


def _as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


structs = []
try:
    for item in idautils.Structs():
        try:
            _idx, sid, name = item
        except Exception:
            continue
        row = {
            "name": str(name),
            "sid": _as_int(sid),
            "size": _as_int(idc.get_struc_size(sid), 0),
            "members": [],
        }
        try:
            for m in idautils.StructMembers(sid):
                offset = 0
                member_name = ""
                member_size = 0
                if isinstance(m, tuple):
                    if len(m) >= 3:
                        offset, member_name, member_size = m[0], m[1], m[2]
                    elif len(m) >= 2:
                        offset, member_name = m[0], m[1]
                        try:
                            member_size = _as_int(idc.get_member_size(sid, _as_int(offset)))
                        except Exception:
                            member_size = 0
                row["members"].append(
                    {
                        "offset": _as_int(offset),
                        "name": str(member_name),
                        "size": _as_int(member_size),
                    }
                )
        except Exception:
            pass
        row["members"].sort(key=lambda x: (int(x.get("offset", 0)), str(x.get("name", ""))))
        structs.append(row)
except Exception:
    traceback.print_exc()

structs.sort(key=lambda x: str(x.get("name", "")))
__result__ = {
    "count": len(structs),
    "structs": structs,
}
'''
    try:
        result = agent.ida_client.execute_script(script=script, context={})
        if result.get("success") and isinstance(result.get("result"), dict):
            payload = result.get("result")
            if isinstance(payload.get("structs"), list):
                return payload
    except Exception:
        pass
    return {"count": 0, "structs": []}


def _member_decl(size: int) -> str:
    if int(size) == 1:
        return "uint8_t"
    if int(size) == 2:
        return "uint16_t"
    if int(size) == 4:
        return "uint32_t"
    if int(size) == 8:
        return "uint64_t"
    return f"uint8_t[{max(int(size), 1)}]"


def _format_struct_definition(row: Dict[str, Any]) -> str:
    name = str(row.get("name", "unnamed"))
    size = int(row.get("size", 0) or 0)
    members = row.get("members", []) or []
    lines = [f"struct {name} {{"]
    for m in members:
        off = int(m.get("offset", 0) or 0)
        mname = str(m.get("name", "field") or "field")
        msize = int(m.get("size", 0) or 0)
        decl = _member_decl(msize)
        if decl.startswith("uint8_t["):
            lines.append(f"    /*0x{off:x}*/ {decl} {mname};")
        else:
            lines.append(f"    /*0x{off:x}*/ {decl} {mname};")
    lines.append(f"}}; // size=0x{size:x} ({size})")
    return "\n".join(lines)


def _struct_signature(row: Dict[str, Any]) -> List[Tuple[int, str, int]]:
    members = row.get("members", []) or []
    sig: List[Tuple[int, str, int]] = []
    for m in members:
        sig.append(
            (
                int(m.get("offset", 0) or 0),
                str(m.get("name", "") or ""),
                int(m.get("size", 0) or 0),
            )
        )
    sig.sort(key=lambda x: (x[0], x[1], x[2]))
    return sig


def _struct_map(snapshot: Dict[str, Any], name_regex: str = "") -> Dict[str, Dict[str, Any]]:
    rows = snapshot.get("structs", []) if isinstance(snapshot, dict) else []
    matcher = re.compile(name_regex) if str(name_regex or "").strip() else None
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("name", "") or "")
        if not name:
            continue
        if matcher and not matcher.search(name):
            continue
        out[name] = row
    return out


def _build_struct_diff(
    before: Dict[str, Any],
    after: Dict[str, Any],
    name_regex: str = "",
) -> Dict[str, Any]:
    before_map = _struct_map(before, name_regex=name_regex)
    after_map = _struct_map(after, name_regex=name_regex)

    new_names = sorted([name for name in after_map.keys() if name not in before_map])
    removed_names = sorted([name for name in before_map.keys() if name not in after_map])
    changed_names = []
    for name in sorted(set(before_map.keys()) & set(after_map.keys())):
        b_sig = _struct_signature(before_map[name])
        a_sig = _struct_signature(after_map[name])
        if b_sig != a_sig or int(before_map[name].get("size", 0) or 0) != int(after_map[name].get("size", 0) or 0):
            changed_names.append(name)

    return {
        "before_total": len(before_map),
        "after_total": len(after_map),
        "new_names": new_names,
        "changed_names": changed_names,
        "removed_names": removed_names,
        "before_map": before_map,
        "after_map": after_map,
    }


def _write_text(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(text or ""))


def _report_dir(base_dir: str, session_id: Optional[str]) -> str:
    os.makedirs(base_dir, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    if session_id:
        suffix = f"{session_id}_{suffix}"
    path = os.path.join(base_dir, suffix)
    os.makedirs(path, exist_ok=True)
    return path


def _load_event_counts(session_db: Optional[str], session_id: Optional[str]) -> Dict[str, int]:
    if (not session_db) or (not session_id):
        return {}
    if not os.path.exists(session_db):
        return {}
    counts: Dict[str, int] = {}
    try:
        with sqlite3.connect(session_db) as conn:
            rows = conn.execute(
                """
                SELECT event, COUNT(*) AS cnt
                FROM session_events
                WHERE session_id=?
                GROUP BY event
                """,
                (session_id,),
            ).fetchall()
        for row in rows:
            event = str(row[0] or "").strip()
            if not event:
                continue
            counts[event] = int(row[1] or 0)
    except Exception:
        return {}
    return counts


def _load_mutation_stats(session_db: Optional[str], session_id: Optional[str]) -> Dict[str, int]:
    stats = {
        "attempt_count": 0,
        "success_count": 0,
        "effective_success_count": 0,
        "noop_count": 0,
        "error_count": 0,
    }
    if (not session_db) or (not session_id):
        return stats
    if not os.path.exists(session_db):
        return stats
    try:
        with sqlite3.connect(session_db) as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM session_events
                WHERE session_id=? AND event='idb_mutation_action'
                ORDER BY seq ASC
                """,
                (session_id,),
            ).fetchall()
        for row in rows:
            payload_raw = str(row[0] or "{}")
            try:
                payload = json.loads(payload_raw)
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                payload = {}
            stats["attempt_count"] += 1
            effective = payload.get("mutation_effective")
            if effective is None:
                effective = payload.get("mutation_success", False)
            if bool(effective):
                stats["success_count"] += 1
                stats["effective_success_count"] += 1
            elif not bool(payload.get("is_error", False)):
                stats["noop_count"] += 1
            if bool(payload.get("is_error", False)):
                stats["error_count"] += 1
    except Exception:
        return stats
    return stats


def _backup_idb(
    agent: ReverseExpertAgentCoreSync,
    backup_dir: str = "",
    backup_tag: str = "pre_recovery",
    backup_filename: str = "",
) -> Dict[str, Any]:
    return agent.ida_client.backup_database(
        backup_dir=backup_dir or None,
        tag=backup_tag,
        filename=backup_filename,
    )


def _build_acceptance_summary_markdown(
    *,
    request: str,
    report_dir: str,
    session_id: str,
    session_db_path: str,
    backup_info: Dict[str, Any],
    snapshot_before: Dict[str, Any],
    snapshot_after: Dict[str, Any],
    struct_summary: Dict[str, Any],
    event_counts: Dict[str, int],
    mutation_stats: Dict[str, int],
    run_timed_out: bool,
    run_interrupted: bool,
    run_error: str,
    has_meaningful_change: bool,
    possible_baseline_pollution: bool,
) -> str:
    lines = [
        "# Reverse Recovery Acceptance Summary",
        "",
        "## Scope",
        f"- request: {request}",
        "",
        "## IDB Backup",
        f"- source_path: {backup_info.get('source_path', '')}",
        f"- backup_path: {backup_info.get('backup_path', '')}",
        f"- method: {backup_info.get('method', '')}",
        "",
        "## IDA Snapshots",
        f"- before_snapshot_desc: {(snapshot_before.get('snapshot') or {}).get('desc', '')}",
        f"- before_snapshot_file: {(snapshot_before.get('snapshot') or {}).get('filename', '')}",
        f"- after_snapshot_desc: {(snapshot_after.get('snapshot') or {}).get('desc', '')}",
        f"- after_snapshot_file: {(snapshot_after.get('snapshot') or {}).get('filename', '')}",
        "",
        "## Execution",
        f"- tool_call_count: {int(event_counts.get('tool_call', 0))}",
        f"- llm_response_count: {int(event_counts.get('llm_response_received', event_counts.get('llm_response', 0)))}",
        f"- idb_mutation_attempt_count: {int(mutation_stats.get('attempt_count', 0))}",
        f"- idb_mutation_effective_count: {int(mutation_stats.get('effective_success_count', mutation_stats.get('success_count', 0)))}",
        f"- idb_mutation_noop_count: {int(mutation_stats.get('noop_count', 0))}",
        f"- run_timed_out: {bool(run_timed_out)}",
        f"- run_interrupted: {bool(run_interrupted)}",
        f"- run_error: {run_error}",
        f"- has_meaningful_change: {bool(has_meaningful_change)}",
        f"- possible_baseline_pollution: {bool(possible_baseline_pollution)}",
        "",
        "## Struct Diff",
        f"- before_total: {struct_summary.get('before_total', 0)}",
        f"- after_total: {struct_summary.get('after_total', 0)}",
        f"- new_count: {struct_summary.get('new_count', 0)}",
        f"- changed_count: {struct_summary.get('changed_count', 0)}",
        f"- removed_count: {struct_summary.get('removed_count', 0)}",
        "",
        "## Artifacts",
        f"- report_dir: {report_dir}",
        f"- session_id: {session_id}",
        f"- session_db: {session_db_path}",
        "- before_structs.json / after_structs.json",
        "- struct_diff_summary.json",
        "- struct_definitions_new_or_changed.txt",
    ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Run reverse expert agent with acceptance report")
    parser.add_argument("--request", required=True, help="Text reverse-analysis request")
    parser.add_argument("--ida-url", default="http://127.0.0.1:5000", help="IDA service URL")
    parser.add_argument("--max-iterations", type=int, default=24, help="Max iterations")
    parser.add_argument(
        "--idapython-kb-dir",
        default="",
        help="Optional IDAPython knowledge base directory for execute_idapython self-repair search/read_file",
    )
    parser.add_argument(
        "--report-dir",
        default=os.path.join(project_root, "..", "logs", "agent_reports"),
        help="Directory to write compare report artifacts",
    )
    args = parser.parse_args()

    model = str(os.getenv("OPENAI_MODEL", REQUIRED_MODEL)).strip()
    if model != REQUIRED_MODEL:
        print(f"[ERROR] Unsupported model '{model}'. Only '{REQUIRED_MODEL}' is allowed.")
        return 1

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] Missing OPENAI_API_KEY")
        return 1
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    request_text = str(args.request or "").strip()
    if not request_text:
        print("[ERROR] Empty request")
        return 1
    if int(args.max_iterations) <= 0:
        print("[ERROR] max-iterations must be > 0")
        return 2

    agent = ReverseExpertAgentCoreSync(
        ida_service_url=args.ida_url,
        openai_api_key=api_key,
        openai_base_url=base_url,
        model=model,
        idapython_kb_dir=args.idapython_kb_dir,
    )

    try:
        health = agent.ida_client.health_check()
        print(f"[OK] IDA Service: {health}")
    except Exception as e:
        print(f"[WARNING] health check failed: {e}")

    print(f"[INFO] Request: {request_text}")
    print("[INFO] Tool profile: struct_recovery")
    print("[INFO] Loop mode: single_policy_loop")

    backup_info: Dict[str, Any] = {}
    snapshot_before: Dict[str, Any] = {}
    snapshot_after: Dict[str, Any] = {}
    try:
        backup_info = _backup_idb(
            agent=agent,
            backup_dir="",
            backup_tag="pre_recovery",
            backup_filename="",
        )
        print(f"[INFO] IDB backup created: {backup_info.get('backup_path', '')}")
    except Exception as e:
        print(f"[ERROR] IDB backup failed: {e}")
        print("[ERROR] Abort run.")
        return 2

    try:
        desc = f"acceptance_before_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        snapshot_before = agent.ida_client.take_database_snapshot(description=desc)
        print(f"[INFO] IDA snapshot(before) created: {(snapshot_before.get('snapshot') or {}).get('filename', '')}")
    except Exception as e:
        print(f"[ERROR] IDA snapshot(before) failed: {e}")
        print("[ERROR] Abort run.")
        return 2

    print("[INFO] Capturing BEFORE struct snapshot...")
    before_structs = _snapshot_structs(agent)
    print(f"[INFO] BEFORE structs: {before_structs.get('count', 0)}")

    run_error = ""
    run_interrupted = False
    result = ""
    interrupt_signal_name = ""
    restore_handlers: Dict[int, Any] = {}

    def _interrupt_handler(signum, frame):
        nonlocal interrupt_signal_name
        try:
            interrupt_signal_name = signal.Signals(signum).name
        except Exception:
            interrupt_signal_name = f"SIGNAL_{int(signum)}"
        raise KeyboardInterrupt(interrupt_signal_name)

    try:
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            restore_handlers[int(sig)] = signal.getsignal(sig)
            signal.signal(sig, _interrupt_handler)
        result = agent.run(user_request=request_text, max_iterations=args.max_iterations)
    except KeyboardInterrupt:
        run_interrupted = True
        signal_name = interrupt_signal_name or "keyboard_interrupt"
        run_error = f"agent run interrupted by {signal_name}"
        result = run_error
        print(f"[ERROR] {run_error}")
        try:
            agent.log_runtime_event(
                "run_interrupted",
                {
                    "signal": signal_name,
                    "reason": run_error,
                },
            )
        except Exception:
            pass
    except Exception as e:
        run_error = f"agent run failed: {e}"
        result = run_error
        print(f"[ERROR] {run_error}")
        try:
            agent.log_runtime_event("run_error", {"reason": run_error})
        except Exception:
            pass
    finally:
        for raw_sig, prev in restore_handlers.items():
            try:
                signal.signal(raw_sig, prev)
            except Exception:
                pass

    try:
        desc = f"acceptance_after_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        snapshot_after = agent.ida_client.take_database_snapshot(description=desc)
        print(f"[INFO] IDA snapshot(after) created: {(snapshot_after.get('snapshot') or {}).get('filename', '')}")
    except Exception as e:
        print(f"[ERROR] IDA snapshot(after) failed: {e}")
        print("[ERROR] Abort run.")
        return 2

    print("[INFO] Capturing AFTER struct snapshot...")
    after_structs = _snapshot_structs(agent)
    print(f"[INFO] AFTER structs: {after_structs.get('count', 0)}")

    session_id = agent.get_last_session_id()
    session_db = agent.get_session_db_path()
    report_dir = _report_dir(args.report_dir, session_id)

    _write_text(os.path.join(report_dir, "agent_final_output.txt"), result)
    _write_text(os.path.join(report_dir, "before_structs.json"), json.dumps(before_structs, ensure_ascii=False, indent=2))
    _write_text(os.path.join(report_dir, "after_structs.json"), json.dumps(after_structs, ensure_ascii=False, indent=2))
    _write_text(os.path.join(report_dir, "idb_backup.json"), json.dumps(backup_info, ensure_ascii=False, indent=2))
    _write_text(os.path.join(report_dir, "ida_snapshot_before.json"), json.dumps(snapshot_before, ensure_ascii=False, indent=2))
    _write_text(os.path.join(report_dir, "ida_snapshot_after.json"), json.dumps(snapshot_after, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print("Reverse expert run completed")
    print("=" * 60)
    print(result)

    if session_id:
        print(f"[LOG] Session ID: {session_id}")
    if session_db:
        print(f"[LOG] Session DB: {session_db}")

    print(f"[REPORT] Artifact directory: {report_dir}")

    print("\n" + "=" * 60)
    print("Struct Definitions (New/Changed)")
    print("=" * 60)
    struct_diff = _build_struct_diff(
        before=before_structs,
        after=after_structs,
        name_regex="",
    )
    summary = {
        "before_total": struct_diff["before_total"],
        "after_total": struct_diff["after_total"],
        "new_count": len(struct_diff["new_names"]),
        "changed_count": len(struct_diff["changed_names"]),
        "removed_count": len(struct_diff["removed_names"]),
        "new_names": struct_diff["new_names"],
        "changed_names": struct_diff["changed_names"],
        "removed_names": struct_diff["removed_names"],
    }
    _write_text(os.path.join(report_dir, "struct_diff_summary.json"), json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    struct_defs_path = os.path.join(report_dir, "struct_definitions_new_or_changed.txt")
    struct_defs_blocks: List[str] = []

    show_limit = 16
    show_names = struct_diff["new_names"] + struct_diff["changed_names"]
    for idx, name in enumerate(show_names):
        row = struct_diff["after_map"].get(name)
        if not row:
            continue
        block = _format_struct_definition(row)
        struct_defs_blocks.append(block)
        if idx < show_limit:
            print(f"\n[STRUCT] {name}")
            print(block)

    if len(show_names) > show_limit:
        print(f"\n... [truncated {len(show_names) - show_limit} structs in terminal, full text in report file]")

    _write_text(struct_defs_path, "\n\n".join(struct_defs_blocks))

    event_counts = _load_event_counts(session_db, session_id)
    mutation_stats = _load_mutation_stats(session_db, session_id)
    acceptance_json = {
        "request": request_text,
        "report_dir": report_dir,
        "session_id": session_id,
        "session_db": session_db,
        "backup": backup_info,
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
        "struct_summary": summary,
        "event_counts": event_counts,
        "mutation_stats": mutation_stats,
        "run_timed_out": False,
        "run_interrupted": bool(run_interrupted),
        "run_error": str(run_error or ""),
    }
    has_struct_change = bool(summary["new_count"] or summary["changed_count"] or summary["removed_count"])
    has_meaningful_change = bool(has_struct_change)
    possible_baseline_pollution = bool(
        (not has_meaningful_change)
        and int(mutation_stats.get("effective_success_count", mutation_stats.get("success_count", 0))) > 0
    )
    acceptance_json["has_meaningful_change"] = has_meaningful_change
    acceptance_json["possible_baseline_pollution"] = possible_baseline_pollution
    _write_text(
        os.path.join(report_dir, "acceptance_summary.json"),
        json.dumps(acceptance_json, ensure_ascii=False, indent=2),
    )
    acceptance_md = _build_acceptance_summary_markdown(
        request=request_text,
        report_dir=report_dir,
        session_id=str(session_id or ""),
        session_db_path=str(session_db or ""),
        backup_info=backup_info,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
        struct_summary=summary,
        event_counts=event_counts,
        mutation_stats=mutation_stats,
        run_timed_out=False,
        run_interrupted=bool(run_interrupted),
        run_error=str(run_error or ""),
        has_meaningful_change=has_meaningful_change,
        possible_baseline_pollution=possible_baseline_pollution,
    )
    acceptance_md_path = os.path.join(report_dir, "acceptance_summary.md")
    _write_text(acceptance_md_path, acceptance_md)
    print(f"[ACCEPTANCE] Summary markdown: {acceptance_md_path}")
    print(f"[ACCEPTANCE] Summary JSON: {os.path.join(report_dir, 'acceptance_summary.json')}")
    print(
        "[ACCEPTANCE] Mutation stats: "
        f"attempt={int(mutation_stats.get('attempt_count', 0))}, "
        f"effective={int(mutation_stats.get('effective_success_count', mutation_stats.get('success_count', 0)))}, "
        f"noop={int(mutation_stats.get('noop_count', 0))}, "
        f"error={int(mutation_stats.get('error_count', 0))}"
    )

    if not session_id:
        print("[ERROR] acceptance check requires session id, but session id is empty.")
        return 3
    tool_call_count = int(event_counts.get("tool_call", 0))
    if tool_call_count <= 0:
        print("[ERROR] acceptance no-op detected: tool_call_count=0")
        print("[ERROR] No effective recovery action executed; investigate LLM/tool flow before accepting this run.")
        return 3

    if run_error:
        print(f"[ERROR] acceptance run failure: {run_error}")
        print("[ERROR] Artifacts were still generated for review, but this run cannot be accepted.")
        return 7

    if int(mutation_stats.get("effective_success_count", mutation_stats.get("success_count", 0))) <= 0:
        print("[ERROR] acceptance no-mutation detected: no effective mutating tool action observed.")
        print("[ERROR] Agent only performed evidence collection, failed mutation steps, or no-op mutations.")
        return 5

    if not has_meaningful_change:
        print("[ERROR] acceptance no-diff detected: structs are unchanged.")
        if possible_baseline_pollution:
            print("[ERROR] Mutations were attempted but no before/after delta was observed.")
            print("[ERROR] This usually means the input IDB was already in a mutated state (baseline pollution).")
        print("[ERROR] Effective reverse recovery was not observed; investigate prompts/subagents/tooling before accepting.")
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
