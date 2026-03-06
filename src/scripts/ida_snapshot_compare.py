#!/usr/bin/env python3
"""Compare before/after IDA snapshots without using agent loop."""
import argparse
import difflib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.ida_client import IDAClient


def _parse_names(values: List[str]) -> List[str]:
    names: List[str] = []
    for value in values:
        for part in str(value).split(","):
            text = part.strip()
            if text:
                names.append(text)
    seen = set()
    out = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _safe_filename(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", str(text or "")).strip("_") or "unnamed"


def _snapshot_pseudocode(client: IDAClient, function_names: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name in function_names:
        try:
            code = client.decompile_function(name=name)
            out[name] = {"ok": True, "text": str(code or "")}
        except Exception as e:
            out[name] = {"ok": False, "error": str(e), "text": ""}
    return out


def _snapshot_structs(client: IDAClient) -> Dict[str, Any]:
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
        result = client.execute_script(script=script, context={})
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
        lines.append(f"    /*0x{off:x}*/ {_member_decl(msize)} {mname};")
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


def _pseudocode_diff(before: str, after: str, func_name: str) -> str:
    lines = list(
        difflib.unified_diff(
            str(before or "").splitlines(),
            str(after or "").splitlines(),
            fromfile=f"before/{func_name}.c",
            tofile=f"after/{func_name}.c",
            lineterm="",
            n=3,
        )
    )
    return "\n".join(lines)


def _write_text(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(text or ""))


def _report_dir(base_dir: str) -> str:
    os.makedirs(base_dir, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(base_dir, f"snapshot_compare_{suffix}")
    os.makedirs(path, exist_ok=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare before/after IDA snapshots without agent")
    parser.add_argument("--ida-url", default="http://127.0.0.1:5000", help="IDA service URL")
    parser.add_argument("--functions", nargs="+", required=True, help="Function names to compare")
    parser.add_argument("--struct-name-regex", default="", help="Regex filter for struct names")
    parser.add_argument(
        "--report-dir",
        default=os.path.join(project_root, "..", "logs", "ida_snapshot_reports"),
        help="Directory to write report artifacts",
    )
    parser.add_argument("--show-diff-lines", type=int, default=220, help="Max diff lines shown per function")
    parser.add_argument("--show-struct-limit", type=int, default=20, help="Max struct definitions in console")
    parser.add_argument(
        "--between-script-file",
        help="Optional IDAPython file to run between before/after snapshots",
    )
    parser.add_argument(
        "--between-template",
        help="Optional ida_scripts template path to run between snapshots (e.g. create_structure.py)",
    )
    parser.add_argument("--template-variables-json", default="", help="JSON object for template variables")
    parser.add_argument("--template-context-json", default="", help="JSON object for template context")
    args = parser.parse_args()

    functions = _parse_names(args.functions)
    if not functions:
        print("[ERROR] no valid functions")
        return 1

    client = IDAClient(base_url=args.ida_url)
    try:
        health = client.health_check()
        print(f"[OK] IDA Service: {health}")
    except Exception as e:
        print(f"[ERROR] health check failed: {e}")
        return 2

    print(f"[INFO] Functions: {functions}")
    print("[INFO] Capturing BEFORE snapshots...")
    before_pseudo = _snapshot_pseudocode(client, functions)
    before_structs = _snapshot_structs(client)
    print(f"[INFO] BEFORE structs: {before_structs.get('count', 0)}")

    if args.between_script_file:
        script_path = Path(args.between_script_file).resolve()
        if not script_path.exists():
            print(f"[ERROR] between script not found: {script_path}")
            return 3
        script_text = script_path.read_text(encoding="utf-8")
        run_result = client.execute_script(script=script_text, context={})
        print(f"[INFO] between script executed: success={run_result.get('success')} execution_time={run_result.get('execution_time')}")
        if run_result.get("stderr"):
            print(f"[WARN] between script stderr:\n{run_result.get('stderr')}")

    if args.between_template:
        variables = json.loads(args.template_variables_json) if args.template_variables_json else {}
        context = json.loads(args.template_context_json) if args.template_context_json else {}
        run_result = client.execute_script_template(
            template_name=args.between_template,
            variables=variables,
            context=context,
        )
        print(f"[INFO] between template executed: success={run_result.get('success')} execution_time={run_result.get('execution_time')}")
        if run_result.get("stderr"):
            print(f"[WARN] between template stderr:\n{run_result.get('stderr')}")

    print("[INFO] Capturing AFTER snapshots...")
    after_pseudo = _snapshot_pseudocode(client, functions)
    after_structs = _snapshot_structs(client)
    print(f"[INFO] AFTER structs: {after_structs.get('count', 0)}")

    report_dir = _report_dir(args.report_dir)
    _write_text(os.path.join(report_dir, "before_structs.json"), json.dumps(before_structs, ensure_ascii=False, indent=2))
    _write_text(os.path.join(report_dir, "after_structs.json"), json.dumps(after_structs, ensure_ascii=False, indent=2))

    print(f"[REPORT] Artifact directory: {report_dir}")

    print("\n" + "=" * 60)
    print("Pseudocode Before/After Diff")
    print("=" * 60)
    for func in functions:
        before_row = before_pseudo.get(func, {})
        after_row = after_pseudo.get(func, {})
        before_text = str(before_row.get("text", ""))
        after_text = str(after_row.get("text", ""))

        _write_text(os.path.join(report_dir, f"pseudocode_before_{_safe_filename(func)}.c"), before_text)
        _write_text(os.path.join(report_dir, f"pseudocode_after_{_safe_filename(func)}.c"), after_text)

        if not before_row.get("ok", False):
            msg = f"[WARN] {func}: before snapshot failed: {before_row.get('error', 'unknown')}"
            print(msg)
            _write_text(os.path.join(report_dir, f"pseudocode_diff_{_safe_filename(func)}.diff"), msg + "\n")
            continue
        if not after_row.get("ok", False):
            msg = f"[WARN] {func}: after snapshot failed: {after_row.get('error', 'unknown')}"
            print(msg)
            _write_text(os.path.join(report_dir, f"pseudocode_diff_{_safe_filename(func)}.diff"), msg + "\n")
            continue

        diff_text = _pseudocode_diff(before_text, after_text, func_name=func)
        _write_text(os.path.join(report_dir, f"pseudocode_diff_{_safe_filename(func)}.diff"), diff_text)
        if not diff_text.strip():
            print(f"[NO_CHANGE] {func}: pseudocode unchanged")
            continue

        print(f"\n[DIFF] {func}")
        diff_lines = diff_text.splitlines()
        max_lines = max(20, int(args.show_diff_lines))
        for line in diff_lines[:max_lines]:
            print(line)
        if len(diff_lines) > max_lines:
            print(f"... [truncated {len(diff_lines) - max_lines} lines, see report file]")

    print("\n" + "=" * 60)
    print("Struct Definitions (New/Changed)")
    print("=" * 60)
    struct_diff = _build_struct_diff(
        before=before_structs,
        after=after_structs,
        name_regex=str(args.struct_name_regex or ""),
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
    show_limit = max(1, int(args.show_struct_limit))
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
        print(f"\n... [truncated {len(show_names) - show_limit} structs, see report file]")
    _write_text(struct_defs_path, "\n\n".join(struct_defs_blocks))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
