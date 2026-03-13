#!/usr/bin/env python3
"""Run reverse expert agent and emit pure-text run trace / evidence artifacts."""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent import ReverseAgentCore
from entrypoints.observability_api import _connect, _fetch_events, _fetch_executed_tool_calls, _fetch_session_summary


DEFAULT_MODEL = "gpt-5.2"
DEFAULT_REPORT_DIR = os.path.join(project_root, "..", "logs", "agent_reports")


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(text or ""))


def _read_text(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _report_dir(base_dir: str, session_id: Optional[str]) -> str:
    os.makedirs(base_dir, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    if session_id:
        suffix = f"{session_id}_{suffix}"
    path = os.path.join(base_dir, suffix)
    os.makedirs(path, exist_ok=True)
    return path


def _truncate_code_for_llm(code: str, max_chars: int = 5000) -> str:
    text = str(code or "").strip()
    if len(text) <= int(max_chars):
        return text
    clipped = text[: int(max_chars)].rstrip()
    return clipped + "\n/* ... truncated ... */"


def _truncate_plain_text(text: str, max_chars: int = 4000) -> str:
    value = str(text or "").strip()
    if len(value) <= int(max_chars):
        return value
    clipped = value[: int(max_chars)].rstrip()
    return clipped + "\n[... truncated ...]"


def _build_eval_case_context(case_id: str, case_spec_path: str, evidence_functions: List[str]) -> str:
    case_value = str(case_id or "").strip()
    spec_path_value = str(case_spec_path or "").strip()
    functions = [str(name or "").strip() for name in list(evidence_functions or []) if str(name or "").strip()]
    spec_text = _read_text(spec_path_value).strip() if spec_path_value else ""
    if not case_value and not functions and not spec_text:
        return ""

    lines = ["## Eval Case Context"]
    if case_value:
        lines.append(f"- case_id: {case_value}")
    if functions:
        lines.append(f"- preferred_functions: {', '.join(functions)}")
    lines.extend(
        [
            "- execution_rule: 若上下文已提供关键函数或成功标准，优先直接对这些函数做 `decompile_function` / `inspect_variable_accesses`，不要先把预算耗在泛化字符串搜索或任务管理上。",
            "",
        ]
    )
    if spec_text:
        lines.extend(
            [
                "## Case Spec",
                _truncate_plain_text(spec_text, max_chars=4000),
            ]
        )
    return "\n".join(lines).strip()


async def _backup_idb(
    agent: Any,
    backup_dir: str = "",
    backup_tag: str = "pre_recovery",
    backup_filename: str = "",
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        agent.ida_client.backup_database,
        backup_dir=backup_dir or None,
        tag=backup_tag,
        filename=backup_filename,
    )


async def _take_snapshot(agent: Any, label: str) -> Dict[str, Any]:
    return await asyncio.to_thread(
        agent.ida_client.take_database_snapshot,
        description=str(label or "").strip(),
    )


def _load_session_observability(session_db: Optional[str], session_id: Optional[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not session_db or not session_id or not os.path.exists(session_db):
        return {}, [], []
    try:
        with _connect(str(session_db)) as conn:
            summary = _fetch_session_summary(conn, str(session_id))
            events = _fetch_events(conn, str(session_id), limit=40, after_seq=0)
            executed = _fetch_executed_tool_calls(conn, str(session_id))
        return summary, events, executed
    except Exception:
        return {}, [], []


def _render_run_trace(
    *,
    request: str,
    session_id: str,
    session_db: str,
    summary: Dict[str, Any],
    events: List[Dict[str, Any]],
    executed_tools: List[Dict[str, Any]],
    backup_info: Dict[str, Any],
    post_backup_info: Dict[str, Any],
    snapshot_before: Dict[str, Any],
    snapshot_after: Dict[str, Any],
    run_error: str,
    run_interrupted: bool,
) -> str:
    mutation = summary.get("mutation_status", {}) if isinstance(summary, dict) else {}
    latest_turn = summary.get("latest_turn", {}) if isinstance(summary, dict) else {}
    latest_batch = summary.get("latest_tool_batch", {}) if isinstance(summary, dict) else {}
    lines = [
        "# Run Trace",
        "",
        "## Session",
        f"- request: {request}",
        f"- session_id: {session_id}",
        f"- session_db: {session_db}",
        f"- status: {summary.get('status', '')}",
        f"- stop_reason: {summary.get('stop_reason', '')}",
        f"- latest_event: {summary.get('latest_event', '')}",
        f"- latest_progress_at: {summary.get('latest_progress_at', '')}",
        f"- run_error: {run_error}",
        f"- run_interrupted: {str(bool(run_interrupted)).lower()}",
        "",
        "## Runtime",
        f"- turn_count: {summary.get('turn_count', 0)}",
        f"- message_count: {summary.get('message_count', 0)}",
        f"- event_count: {summary.get('event_count', 0)}",
        f"- executed_tool_call_count: {summary.get('executed_tool_call_count', 0)}",
        f"- stalled: {str(bool(summary.get('stalled', False))).lower()}",
        f"- stalled_seconds: {int(summary.get('stalled_seconds', 0) or 0)}",
        "",
        "## Mutation",
        f"- attempt_count: {int(mutation.get('attempt_count', 0) or 0)}",
        f"- effective_mutation_count: {int(mutation.get('effective_mutation_count', 0) or 0)}",
        f"- recent_effective_tools: {', '.join(list(mutation.get('recent_effective_tools', []) or []))}",
        f"- no_mutation_turns: {int(mutation.get('no_mutation_turns', 0) or 0)}",
        "",
        "## Latest Turn",
    ]
    if latest_turn:
        for key in ("turn_id", "iteration", "status", "phase", "agent_id", "agent_name", "created_at"):
            lines.append(f"- {key}: {latest_turn.get(key, '')}")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Latest Tool Batch"])
    if latest_batch:
        lines.append(f"- turn_id: {latest_batch.get('turn_id', '')}")
        lines.append(f"- error_count: {int(latest_batch.get('error_count', 0) or 0)}")
        lines.append(f"- duration_ms: {int(latest_batch.get('duration_ms', 0) or 0)}")
        recent_tools = latest_batch.get("recent_tools", []) or []
        if recent_tools:
            for row in recent_tools[:8]:
                if isinstance(row, dict):
                    lines.append(f"- tool: {row.get('tool_name', '')} error={str(bool(row.get('is_error', False))).lower()}")
        else:
            lines.append("- (none)")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Recent Events"])
    if events:
        for row in events[-12:]:
            payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
            lines.append(
                "- "
                + f"seq={int(row.get('seq', 0) or 0)} "
                + f"event={row.get('event', '')} "
                + f"turn_id={payload.get('turn_id', '')} "
                + f"status={payload.get('status', '')}"
            )
    else:
        lines.append("- (none)")
    lines.extend(["", "## Effective Tool Results"])
    effective_rows = [row for row in executed_tools if bool(row.get("mutation_effective", False))]
    if effective_rows:
        for row in effective_rows[-10:]:
            lines.append(
                "- "
                + f"{row.get('tool_name', '')}: "
                + str(row.get("result_preview", "") or "").strip().replace("\n", " ")
            )
    else:
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "## IDB Artifacts",
            f"- pre_recovery_backup: {backup_info.get('backup_path', '')}",
            f"- post_recovery_backup: {post_backup_info.get('backup_path', '')}",
            f"- before_snapshot: {(snapshot_before.get('snapshot') or {}).get('filename', '')}",
            f"- after_snapshot: {(snapshot_after.get('snapshot') or {}).get('filename', '')}",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _extract_function_names_from_output(text: str) -> List[str]:
    names: List[str] = []
    patterns = [
        r"-\s+([A-Za-z_][A-Za-z0-9_:~]*|sub_[0-9A-Fa-f]+)@0x",
        r"\b(main|sub_[0-9A-Fa-f]+|[A-Za-z_][A-Za-z0-9_:~]*::[A-Za-z_~][A-Za-z0-9_:~]*)\b",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, str(text or "")):
            value = str(match or "").strip()
            if not value:
                continue
            if value not in names:
                names.append(value)
    return names[:12]


def _extract_function_names_from_tool_results(executed_tools: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    patterns = [
        r"函数\s+([A-Za-z_][A-Za-z0-9_:~]*|sub_[0-9A-Fa-f]+)\s+中完成类型设置",
        r"已更新函数注释：([A-Za-z_][A-Za-z0-9_:~]*|sub_[0-9A-Fa-f]+)\s+@",
    ]
    for row in executed_tools:
        preview = str(row.get("result_preview", "") or "")
        for pattern in patterns:
            for match in re.findall(pattern, preview):
                value = str(match or "").strip()
                if value and value not in names:
                    names.append(value)
    return names[:12]


def _resolve_available_name(requested: str, available: List[str]) -> str:
    target = str(requested or "").strip()
    if not target:
        return ""
    if target in available:
        return target
    for name in available:
        if name.endswith(f"::{target}"):
            return name
    lowered = target.lower()
    for name in available:
        if str(name).lower() == lowered:
            return name
    return ""


def _resolve_function_ref(requested: str, functions: List[Dict[str, Any]]) -> Dict[str, Any]:
    available_names = [str(row.get("name", "") or "").strip() for row in functions if str(row.get("name", "") or "").strip()]
    resolved_name = _resolve_available_name(requested, available_names)
    if not resolved_name:
        return {"requested": requested, "resolved": "", "ea": None}
    for row in functions:
        if str(row.get("name", "") or "").strip() == resolved_name:
            return {
                "requested": requested,
                "resolved": resolved_name,
                "ea": row.get("ea"),
            }
    return {"requested": requested, "resolved": resolved_name, "ea": None}


async def _capture_decompilation_for_db(
    agent: Any,
    db_path: str,
    requested_names: List[str],
    preferred_refs: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, str]]:
    await asyncio.to_thread(
        agent.ida_client.open_database,
        input_path=str(db_path),
        run_auto_analysis=False,
        save_current=False,
    )
    functions = await asyncio.to_thread(agent.ida_client.list_functions)
    output: Dict[str, Dict[str, str]] = {}
    by_ea: Dict[int, Dict[str, Any]] = {}
    for row in functions:
        try:
            ea_value = int(row.get("ea"))
        except Exception:
            continue
        by_ea[ea_value] = row
    for requested in requested_names:
        preferred = (preferred_refs or {}).get(requested, {}) if isinstance(preferred_refs, dict) else {}
        resolved_ref = {}
        preferred_ea = preferred.get("ea")
        if preferred_ea is not None:
            try:
                resolved_ref = {
                    "requested": requested,
                    "resolved": str((by_ea.get(int(preferred_ea), {}) or {}).get("name", "") or ""),
                    "ea": int(preferred_ea),
                }
            except Exception:
                resolved_ref = {}
        if not resolved_ref:
            resolved_ref = _resolve_function_ref(requested, functions)
        resolved = str(resolved_ref.get("resolved", "") or "")
        resolved_ea = resolved_ref.get("ea")
        if not resolved and resolved_ea is None:
            output[requested] = {"requested": requested, "resolved": "", "ea": "", "status": "missing", "code": ""}
            continue
        try:
            code = await asyncio.to_thread(agent.ida_client.decompile_function, function_name=resolved or None, ea=resolved_ea)
            output[requested] = {
                "requested": requested,
                "resolved": resolved,
                "ea": "" if resolved_ea is None else str(int(resolved_ea)),
                "status": "ok",
                "code": str(code or "").strip(),
            }
        except Exception as e:
            output[requested] = {
                "requested": requested,
                "resolved": resolved,
                "ea": "" if resolved_ea is None else str(int(resolved_ea)),
                "status": f"error: {e}",
                "code": "",
            }
    return output


async def _collect_decompilation_evidence(
    agent: Any,
    *,
    current_db_path: str,
    before_snapshot_path: str,
    after_snapshot_path: str,
    requested_names: List[str],
) -> List[Dict[str, str]]:
    ordered = []
    seen = set()
    for name in requested_names:
        value = str(name or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    if not ordered:
        return []

    before_map = await _capture_decompilation_for_db(agent, before_snapshot_path, ordered)
    preferred_refs: Dict[str, Dict[str, Any]] = {}
    for name in ordered:
        row = before_map.get(name, {})
        if str(row.get("status", "") or "") != "ok":
            continue
        ea_value = str(row.get("ea", "") or "").strip()
        if ea_value:
            preferred_refs[name] = {"ea": int(ea_value)}
    after_map = await _capture_decompilation_for_db(agent, after_snapshot_path, ordered, preferred_refs=preferred_refs)
    if current_db_path:
        try:
            await asyncio.to_thread(
                agent.ida_client.open_database,
                input_path=str(current_db_path),
                run_auto_analysis=False,
                save_current=False,
            )
        except Exception:
            pass

    rows: List[Dict[str, str]] = []
    for name in ordered:
        before_row = before_map.get(name, {})
        after_row = after_map.get(name, {})
        rows.append(
            {
                "requested": name,
                "before_resolved": str(before_row.get("resolved", "") or ""),
                "before_ea": str(before_row.get("ea", "") or ""),
                "before_status": str(before_row.get("status", "") or "missing"),
                "before_code": str(before_row.get("code", "") or ""),
                "after_resolved": str(after_row.get("resolved", "") or ""),
                "after_ea": str(after_row.get("ea", "") or ""),
                "after_status": str(after_row.get("status", "") or "missing"),
                "after_code": str(after_row.get("code", "") or ""),
            }
        )
    return rows


async def _collect_current_decompilation_evidence(
    agent: Any,
    *,
    requested_names: List[str],
) -> List[Dict[str, str]]:
    ordered = []
    seen = set()
    for name in requested_names:
        value = str(name or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    if not ordered:
        return []

    db_info = await asyncio.to_thread(agent.ida_client.get_db_info)
    if not bool(db_info.get("success")):
        raise RuntimeError(str(db_info.get("error") or "get_db_info failed"))

    info = db_info.get("result")
    if not isinstance(info, dict):
        raise RuntimeError(f"unexpected get_db_info payload: {type(info).__name__}")

    current_db_path = str(info.get("path", "") or "").strip()
    if not current_db_path:
        raise RuntimeError("empty current db path")

    current_map = await _capture_decompilation_for_db(agent, current_db_path, ordered)
    rows: List[Dict[str, str]] = []
    for name in ordered:
        current_row = current_map.get(name, {})
        rows.append(
            {
                "requested": name,
                "before_resolved": "",
                "before_ea": "",
                "before_status": "unavailable",
                "before_code": "",
                "after_resolved": str(current_row.get("resolved", "") or ""),
                "after_ea": str(current_row.get("ea", "") or ""),
                "after_status": str(current_row.get("status", "") or "missing"),
                "after_code": str(current_row.get("code", "") or ""),
            }
        )
    return rows


async def _summarize_decompilation_diff_with_llm(
    *,
    model: str,
    api_key: str,
    base_url: str,
    profile: str,
    request_text: str,
    decompile_rows: List[Dict[str, str]],
) -> str:
    compare_rows = []
    for row in decompile_rows:
        before_status = str(row.get("before_status", "") or "")
        after_status = str(row.get("after_status", "") or "")
        if before_status != "ok" and after_status != "ok":
            continue
        compare_rows.append(row)
    if not compare_rows:
        return ""

    blocks: List[str] = []
    for row in compare_rows[:6]:
        blocks.extend(
            [
                f"### {str(row.get('requested', '') or '')}",
                f"- before_status: {str(row.get('before_status', '') or '')}",
                f"- after_status: {str(row.get('after_status', '') or '')}",
                "",
                "#### Before",
                "```c",
                _truncate_code_for_llm(str(row.get("before_code", "") or "")),
                "```",
                "",
                "#### After",
                "```c",
                _truncate_code_for_llm(str(row.get("after_code", "") or "")),
                "```",
                "",
            ]
        )

    system_prompt = (
        "你是一名逆向分析复核助手。"
        "你要比较同一批函数在恢复前后的反编译伪代码，输出简洁、可复核的修改概述。"
        "重点关注：结构体类型收敛、字段名/成员访问、函数指针语义、参数/返回值类型、控制流或数据流理解是否更明确。"
        "不要泛泛而谈，不要重复整段代码。"
        "禁止补写新的结构体定义、伪代码、C 声明或修复建议。"
        "输出必须是 Markdown。"
    )
    human_prompt = "\n".join(
        [
            f"- profile: {str(profile or '').strip()}",
            f"- request: {str(request_text or '').strip()}",
            "",
            "请基于下面的 before/after 伪代码对比，输出：",
            "1. `## 修改概述`：3-6 条，说明整体变化。",
            "2. `## 函数级变化`：每个函数 2-4 条，说明恢复前后最关键的可观察差异。",
            "3. `## 剩余缺口`：列出仍然不清楚、未收敛或证据不足的点。",
            "",
            "要求：",
            "- 只根据给定 before/after 文本做判断。",
            "- 如果某函数 before 缺失或 unavailable，要明确写出“无法直接做前后对比”。",
            "- 对 struct_recovery，优先强调类型、成员访问和命名收敛。",
            "- 对 attack_surface/general_reverse，优先强调调用链、外部交互和语义理解是否更清晰。",
            "- 不要输出代码块，不要补写新的结构体布局，不要给实现方案。",
            "",
            "## Before/After Pseudocode",
            "\n".join(blocks),
        ]
    )

    llm = ChatOpenAI(
        model=str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        temperature=0,
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
    )
    response = await asyncio.to_thread(
        llm.invoke,
        [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)],
    )
    return str(getattr(response, "content", "") or "").strip()


def _build_evidence_markdown(
    *,
    case_id: str,
    profile: str,
    request_text: str,
    final_output_text: str,
    decompile_rows: List[Dict[str, str]],
    decompile_compare_md: str,
    backup_info: Dict[str, Any],
    post_backup_info: Dict[str, Any],
    snapshot_before: Dict[str, Any],
    snapshot_after: Dict[str, Any],
) -> str:
    lines = [
        "# Evidence",
        "",
        "## Case Context",
        f"- case_id: {case_id}",
        f"- profile: {profile}",
        f"- request: {request_text}",
        f"- pre_recovery_backup: {backup_info.get('backup_path', '')}",
        f"- post_recovery_backup: {post_backup_info.get('backup_path', '')}",
        f"- before_snapshot: {(snapshot_before.get('snapshot') or {}).get('filename', '')}",
        f"- after_snapshot: {(snapshot_after.get('snapshot') or {}).get('filename', '')}",
        "",
        "## Agent Final Output",
        final_output_text.strip() if final_output_text.strip() else "(empty)",
        "",
        "## Pseudocode Diff Summary",
        decompile_compare_md.strip() if decompile_compare_md.strip() else "- unavailable",
        "",
        "## Decompiled Evidence",
    ]
    if not decompile_rows:
        lines.append("- no target functions available")
    else:
        for row in decompile_rows:
            lines.extend(
                [
                    "",
                    f"### {row.get('requested', '')}",
                    f"- before_resolved: {row.get('before_resolved', '')}",
                    f"- before_ea: {row.get('before_ea', '')}",
                    f"- before_status: {row.get('before_status', '')}",
                    f"- after_resolved: {row.get('after_resolved', '')}",
                    f"- after_ea: {row.get('after_ea', '')}",
                    f"- after_status: {row.get('after_status', '')}",
                    "",
                    "#### Before",
                    "```c",
                    row.get("before_code", "") or "// unavailable",
                    "```",
                    "",
                    "#### After",
                    "```c",
                    row.get("after_code", "") or "// unavailable",
                    "```",
                ]
            )
    return "\n".join(lines).strip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run reverse expert agent and emit pure-text evidence")
    parser.add_argument("--request", required=True, help="Text reverse-analysis request")
    parser.add_argument("--ida-url", default="http://127.0.0.1:5000", help="IDA service URL")
    parser.add_argument("--max-iterations", type=int, default=24, help="Max iterations")
    parser.add_argument(
        "--agent-core",
        choices=["struct_recovery", "dispatcher"],
        default="struct_recovery",
        help="Agent core entrypoint: direct struct_recovery or reverse dispatcher",
    )
    parser.add_argument(
        "--agent-profile",
        choices=["struct_recovery", "attack_surface", "general_reverse"],
        default="struct_recovery",
        help="Agent profile executed by the generic reverse runtime",
    )
    parser.add_argument("--idapython-kb-dir", default="", help="Optional IDAPython KB dir")
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="Directory to write report artifacts")
    parser.add_argument("--case-id", default="", help="Optional eval case id")
    parser.add_argument("--case-spec-path", default="", help="Optional case spec markdown path")
    parser.add_argument("--evidence-function", action="append", default=[], help="Preferred function names for before/after decompile evidence")
    return parser


async def run_from_namespace(args: argparse.Namespace) -> int:
    model = str(os.getenv("OPENAI_MODEL", DEFAULT_MODEL)).strip() or DEFAULT_MODEL
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] Missing OPENAI_API_KEY")
        return 1
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    base_request_text = str(args.request or "").strip()
    if not base_request_text:
        print("[ERROR] Empty request")
        return 1
    if int(args.max_iterations) <= 0:
        print("[ERROR] max-iterations must be > 0")
        return 2

    case_context = _build_eval_case_context(
        str(getattr(args, "case_id", "") or ""),
        str(getattr(args, "case_spec_path", "") or ""),
        [str(name or "").strip() for name in list(getattr(args, "evidence_function", []) or [])],
    )
    request_text = base_request_text if not case_context else f"{base_request_text}\n\n{case_context}"

    agent = ReverseAgentCore(
        ida_service_url=args.ida_url,
        openai_api_key=api_key,
        openai_base_url=base_url,
        model=model,
        idapython_kb_dir=args.idapython_kb_dir,
        agent_profile=str(args.agent_profile or "struct_recovery"),
        runtime_name=f"ReverseAgentCore[{str(args.agent_profile or 'struct_recovery')}]",
    )

    try:
        health = await asyncio.to_thread(agent.ida_client.health_check)
        print(f"[OK] IDA Service: {health}")
    except Exception as e:
        print(f"[WARNING] health check failed: {e}")

    print(f"[INFO] Request: {base_request_text}")
    if case_context:
        print(f"[INFO] Eval case context injected: case_id={str(getattr(args, 'case_id', '') or '').strip()}")
    print(f"[INFO] Agent core: {args.agent_core}")
    print(f"[INFO] Agent profile: {args.agent_profile}")
    print(f"[INFO] Tool profile: {args.agent_profile}")
    print("[INFO] Loop mode: single_policy_loop")

    backup_info: Dict[str, Any] = {}
    post_backup_info: Dict[str, Any] = {}
    snapshot_before: Dict[str, Any] = {}
    snapshot_after: Dict[str, Any] = {}
    try:
        backup_info = await _backup_idb(agent=agent, backup_dir="", backup_tag="pre_recovery", backup_filename="")
        print(f"[INFO] IDB backup created: {backup_info.get('backup_path', '')}")
    except Exception as e:
        print(f"[WARN] IDB backup failed: {e}")
        backup_info = {}

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
        result = await agent.run(user_request=request_text, max_iterations=args.max_iterations)
    except KeyboardInterrupt:
        run_interrupted = True
        signal_name = interrupt_signal_name or "keyboard_interrupt"
        run_error = f"agent run interrupted by {signal_name}"
        result = run_error
        print(f"[ERROR] {run_error}")
        try:
            agent.log_runtime_event("run_stopped", {"status": "interrupted", "signal": signal_name, "reason": run_error})
        except Exception:
            pass
    except Exception as e:
        run_error = f"agent run failed: {e}"
        result = run_error
        print(f"[ERROR] {run_error}")
        try:
            agent.log_runtime_event("run_stopped", {"status": "failed", "reason": run_error})
        except Exception:
            pass
    finally:
        for raw_sig, prev in restore_handlers.items():
            try:
                signal.signal(raw_sig, prev)
            except Exception:
                pass

    try:
        post_backup_info = await _backup_idb(agent=agent, backup_dir="", backup_tag="post_recovery", backup_filename="")
        print(f"[INFO] Post-recovery IDB backup created: {post_backup_info.get('backup_path', '')}")
    except Exception as e:
        print(f"[WARN] Post-recovery IDB backup failed: {e}")
        post_backup_info = {}

    session_id = agent.get_last_session_id()
    session_db = agent.get_session_db_path()
    report_dir = _report_dir(args.report_dir, session_id)
    try:
        logger_obj = getattr(agent, "session_logger", None)
        if logger_obj is not None:
            logger_obj.set_log_path(report_dir)
    except Exception:
        pass

    summary, events, executed_tools = _load_session_observability(session_db, session_id)
    run_trace_md = _render_run_trace(
        request=request_text,
        session_id=str(session_id or ""),
        session_db=str(session_db or ""),
        summary=summary,
        events=events,
        executed_tools=executed_tools,
        backup_info=backup_info,
        post_backup_info=post_backup_info,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
        run_error=str(run_error or ""),
        run_interrupted=bool(run_interrupted),
    )

    requested_functions: List[str] = []
    for name in list(args.evidence_function or []):
        value = str(name or "").strip()
        if value and value not in requested_functions:
            requested_functions.append(value)
    for name in _extract_function_names_from_tool_results(executed_tools):
        if name not in requested_functions:
            requested_functions.append(name)
    for name in _extract_function_names_from_output(result):
        if name not in requested_functions:
            requested_functions.append(name)
    requested_functions = requested_functions[:8]

    decompile_rows: List[Dict[str, str]] = []
    decompile_compare_md = ""
    before_backup_path = str((backup_info or {}).get("backup_path", "") or "")
    after_backup_path = str((post_backup_info or {}).get("backup_path", "") or "")
    current_db_path = str((post_backup_info or {}).get("source_path", "") or (backup_info or {}).get("source_path", "") or "")
    if requested_functions and before_backup_path and after_backup_path:
        try:
            decompile_rows = await _collect_decompilation_evidence(
                agent,
                current_db_path=current_db_path,
                before_snapshot_path=before_backup_path,
                after_snapshot_path=after_backup_path,
                requested_names=requested_functions,
            )
        except Exception as e:
            decompile_rows = [
                {
                    "requested": "(evidence collection)",
                    "before_resolved": "",
                    "before_status": f"error: {e}",
                    "before_code": "",
                    "after_resolved": "",
                    "after_status": f"error: {e}",
                    "after_code": "",
                }
            ]
    elif requested_functions:
        try:
            decompile_rows = await _collect_current_decompilation_evidence(
                agent,
                requested_names=requested_functions,
            )
        except Exception as e:
            decompile_rows = [
                {
                    "requested": "(evidence collection)",
                    "before_resolved": "",
                    "before_status": f"error: {e}",
                    "before_code": "",
                    "after_resolved": "",
                    "after_status": f"error: {e}",
                    "after_code": "",
                }
            ]

    if decompile_rows:
        try:
            decompile_compare_md = await _summarize_decompilation_diff_with_llm(
                model=model,
                api_key=str(api_key or ""),
                base_url=str(base_url or ""),
                profile=str(args.agent_profile or ""),
                request_text=request_text,
                decompile_rows=decompile_rows,
            )
            if decompile_compare_md:
                print("[INFO] LLM pseudocode diff summary generated")
        except Exception as e:
            decompile_compare_md = f"- unavailable: {e}"

    evidence_md = _build_evidence_markdown(
        case_id=str(args.case_id or ""),
        profile=str(args.agent_profile or ""),
        request_text=request_text,
        final_output_text=result,
        decompile_rows=decompile_rows,
        decompile_compare_md=decompile_compare_md,
        backup_info=backup_info,
        post_backup_info=post_backup_info,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
    )

    _write_text(os.path.join(report_dir, "run_trace.md"), run_trace_md)
    _write_text(os.path.join(report_dir, "evidence.md"), evidence_md)

    print("\n" + "=" * 60)
    print("Reverse expert run completed")
    print("=" * 60)
    print(result)
    if session_id:
        print(f"[LOG] Session ID: {session_id}")
    if session_db:
        print(f"[LOG] Session DB: {session_db}")
    print(f"[REPORT] Artifact directory: {report_dir}")
    print(f"[REPORT] Run trace: {os.path.join(report_dir, 'run_trace.md')}")
    print(f"[REPORT] Evidence: {os.path.join(report_dir, 'evidence.md')}")

    if not session_id:
        print("[ERROR] Missing session ID")
        return 3
    if run_error:
        print(f"[ERROR] Run failed: {run_error}")
        return 7
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(asyncio.run(run_from_namespace(args)))


if __name__ == "__main__":
    raise SystemExit(main())
