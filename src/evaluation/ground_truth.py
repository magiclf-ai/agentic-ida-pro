"""Pure-text case spec loading and LLM judging helpers for eval runs."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
JUDGE_SYSTEM_PROMPT_PATH = PROJECT_ROOT / "src" / "prompts" / "eval" / "case_judge_system.md"
DEFAULT_MODEL = "gpt-5.2"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def load_case_spec_text(spec_path: str) -> str:
    path = Path(str(spec_path or "")).resolve()
    if not path.exists():
        raise FileNotFoundError(f"case spec not found: {path}")
    return _read_text(path).strip() + "\n"


def load_markdown_artifact(path: str) -> str:
    target = Path(str(path or "")).resolve()
    if not target.exists():
        return ""
    return _read_text(target).strip() + "\n"


def load_case_artifacts(case_dir: str) -> Dict[str, str]:
    root = Path(str(case_dir or "")).resolve()
    state_root = root / ".eval_state"
    return {
        "run_trace": load_markdown_artifact(str(root / "run_trace.md")),
        "evidence": load_markdown_artifact(str(root / "evidence.md")),
        "stdout": load_markdown_artifact(str(state_root / "stdout.log")) or load_markdown_artifact(str(root / "stdout.log")),
        "stderr": load_markdown_artifact(str(state_root / "stderr.log")) or load_markdown_artifact(str(root / "stderr.log")),
        "watch": load_markdown_artifact(str(state_root / "watch.log")) or load_markdown_artifact(str(root / "watch.log")),
        "service": load_markdown_artifact(str(state_root / "service.log")) or load_markdown_artifact(str(root / "service.log")),
    }


def _judge_system_prompt() -> str:
    if JUDGE_SYSTEM_PROMPT_PATH.exists():
        return _read_text(JUDGE_SYSTEM_PROMPT_PATH).strip()
    return (
        "你是逆向评测裁判。"
        "必须根据 case spec、run_trace、evidence 中的纯文本证据裁决。"
        "结构体恢复以 after 反编译是否真实收敛为主，不以 exit code 或结构体 diff 单独裁决。"
    )


def _format_judge_prompt(
    *,
    case_id: str,
    profile: str,
    run_exit_code: int,
    run_status: str,
    stop_reason: str,
    case_spec_text: str,
    run_trace_text: str,
    evidence_text: str,
) -> str:
    return (
        f"# Case\n"
        f"- case_id: {case_id}\n"
        f"- profile: {profile}\n"
        f"- run_exit_code: {int(run_exit_code)}\n"
        f"- run_status: {run_status}\n"
        f"- stop_reason: {stop_reason}\n"
        "\n"
        "## Case Spec\n"
        f"{case_spec_text.strip()}\n"
        "\n"
        "## Run Trace\n"
        f"{run_trace_text.strip()}\n"
        "\n"
        "## Evidence\n"
        f"{evidence_text.strip()}\n"
        "\n"
        "请调用 submit_eval_verdict，给出最终 verdict。"
    ).strip()


def _invoke_judge(*, system_prompt: str, human_prompt: str) -> Dict[str, str]:
    api_key = str(os.getenv("OPENAI_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("missing OPENAI_API_KEY")
    base_url = str(os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).strip()
    model = str(os.getenv("OPENAI_MODEL", DEFAULT_MODEL)).strip() or DEFAULT_MODEL

    llm = ChatOpenAI(
        model=model,
        temperature=0,
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
    )

    @tool("submit_eval_verdict", parse_docstring=True, error_on_invalid_docstring=True)
    def submit_eval_verdict(verdict: str, summary: str, evidence: str = "", risks: str = "") -> str:
        """Submit one case verdict.

        Args:
            verdict: 必须为 pass / partial / fail / infra_fail 之一。
            summary: 最终结论摘要。
            evidence: 支撑裁决的关键证据。
            risks: 风险、缺口或残留不确定性。

        Returns:
            纯文本确认信息。
        """
        return "OK: verdict submitted"

    runnable = llm.bind_tools([submit_eval_verdict], tool_choice="submit_eval_verdict")
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]

    for _ in range(2):
        response = runnable.invoke(messages)
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        for call in tool_calls:
            if str(call.get("name", "") or "") != "submit_eval_verdict":
                continue
            args = call.get("args", {}) if isinstance(call, dict) else {}
            if not isinstance(args, dict):
                continue
            verdict = str(args.get("verdict", "") or "").strip().lower()
            if verdict not in {"pass", "partial", "fail", "infra_fail"}:
                continue
            return {
                "verdict": verdict,
                "summary": str(args.get("summary", "") or "").strip(),
                "evidence": str(args.get("evidence", "") or "").strip(),
                "risks": str(args.get("risks", "") or "").strip(),
            }
        messages.append(HumanMessage(content="必须调用 submit_eval_verdict，并且 verdict 只能是 pass / partial / fail / infra_fail。"))
    raise RuntimeError("judge did not submit verdict")


def judge_case(
    *,
    case_id: str,
    profile: str,
    case_spec_text: str,
    run_trace_text: str,
    evidence_text: str,
    run_exit_code: int,
    run_status: str,
    stop_reason: str,
) -> Dict[str, str]:
    if not str(case_spec_text or "").strip():
        return {
            "verdict": "infra_fail",
            "summary": "missing case spec",
            "evidence": "case spec file is missing or empty",
            "risks": "",
        }
    if not str(run_trace_text or "").strip():
        return {
            "verdict": "infra_fail",
            "summary": "missing run trace",
            "evidence": "run_trace.md is missing or empty",
            "risks": "",
        }
    if not str(evidence_text or "").strip():
        return {
            "verdict": "infra_fail",
            "summary": "missing evidence",
            "evidence": "evidence.md is missing or empty",
            "risks": "",
        }

    system_prompt = _judge_system_prompt()
    human_prompt = _format_judge_prompt(
        case_id=case_id,
        profile=profile,
        run_exit_code=run_exit_code,
        run_status=run_status,
        stop_reason=stop_reason,
        case_spec_text=case_spec_text,
        run_trace_text=run_trace_text,
        evidence_text=evidence_text,
    )
    return _invoke_judge(system_prompt=system_prompt, human_prompt=human_prompt)


def build_case_verdict_markdown(
    *,
    case_id: str,
    profile: str,
    run_exit_code: int,
    run_status: str,
    stop_reason: str,
    verdict_payload: Dict[str, str],
) -> str:
    verdict = str(verdict_payload.get("verdict", "") or "").strip().lower() or "infra_fail"
    summary = str(verdict_payload.get("summary", "") or "").strip()
    evidence = str(verdict_payload.get("evidence", "") or "").strip()
    risks = str(verdict_payload.get("risks", "") or "").strip()
    lines = [
        "# Eval Verdict",
        "",
        f"- case_id: {case_id}",
        f"- profile: {profile}",
        f"- run_exit_code: {int(run_exit_code)}",
        f"- run_status: {run_status}",
        f"- stop_reason: {stop_reason}",
        f"- verdict: {verdict}",
        f"- summary: {summary}",
        "",
        "## Evidence",
    ]
    if evidence:
        lines.append(evidence)
    else:
        lines.append("- (none)")
    lines.extend(["", "## Risks"])
    if risks:
        lines.append(risks)
    else:
        lines.append("- (none)")
    return "\n".join(lines).strip() + "\n"


def verdict_to_case_status(verdict: str) -> str:
    value = str(verdict or "").strip().lower()
    if value in {"pass", "partial"}:
        return "completed"
    return "failed"


def build_progress_markdown(
    *,
    run_id: str,
    total_case_count: int,
    current_case_id: str,
    results: List[Dict[str, Any]],
) -> str:
    pass_count = len([row for row in results if str(row.get("verdict", "")).lower() == "pass"])
    partial_count = len([row for row in results if str(row.get("verdict", "")).lower() == "partial"])
    fail_count = len([row for row in results if str(row.get("verdict", "")).lower() == "fail"])
    infra_fail_count = len([row for row in results if str(row.get("verdict", "")).lower() == "infra_fail"])
    lines = [
        "# Eval Progress",
        "",
        f"- run_id: {run_id}",
        f"- total_case_count: {int(total_case_count)}",
        f"- current_case_id: {current_case_id}",
        f"- pass_case_count: {pass_count}",
        f"- partial_case_count: {partial_count}",
        f"- fail_case_count: {fail_count}",
        f"- infra_fail_case_count: {infra_fail_count}",
        "",
        "## Cases",
    ]
    if not results:
        lines.append("- (none)")
    else:
        for row in results:
            lines.append(
                "- "
                + f"{row.get('case_id', '')}: "
                + f"verdict={row.get('verdict', '')} "
                + f"run_exit_code={row.get('run_exit_code', '')} "
                + f"summary={row.get('summary', '')}"
            )
    return "\n".join(lines).strip() + "\n"


def build_summary_markdown(
    *,
    run_id: str,
    results: List[Dict[str, Any]],
    stop_requested: bool,
    stop_reason: str,
) -> str:
    pass_count = len([row for row in results if str(row.get("verdict", "")).lower() == "pass"])
    partial_count = len([row for row in results if str(row.get("verdict", "")).lower() == "partial"])
    fail_count = len([row for row in results if str(row.get("verdict", "")).lower() == "fail"])
    infra_fail_count = len([row for row in results if str(row.get("verdict", "")).lower() == "infra_fail"])
    lines = [
        "# Eval Summary",
        "",
        f"- run_id: {run_id}",
        f"- case_count: {len(results)}",
        f"- pass_case_count: {pass_count}",
        f"- partial_case_count: {partial_count}",
        f"- fail_case_count: {fail_count}",
        f"- infra_fail_case_count: {infra_fail_count}",
        f"- stop_requested: {str(bool(stop_requested)).lower()}",
        f"- stop_reason: {stop_reason}",
        "",
        "## Results",
    ]
    if not results:
        lines.append("- (none)")
    else:
        for row in results:
            lines.append(
                "- "
                + f"{row.get('case_id', '')}: "
                + f"verdict={row.get('verdict', '')} "
                + f"run_exit_code={row.get('run_exit_code', '')} "
                + f"summary={row.get('summary', '')}"
            )
    return "\n".join(lines).strip() + "\n"


def build_root_verdict_markdown(*, run_id: str, results: List[Dict[str, Any]]) -> str:
    pass_count = len([row for row in results if str(row.get("verdict", "")).lower() == "pass"])
    partial_count = len([row for row in results if str(row.get("verdict", "")).lower() == "partial"])
    fail_count = len([row for row in results if str(row.get("verdict", "")).lower() == "fail"])
    infra_fail_count = len([row for row in results if str(row.get("verdict", "")).lower() == "infra_fail"])
    lines = [
        "# Eval Verdict",
        "",
        f"- run_id: {run_id}",
        f"- pass_case_count: {pass_count}",
        f"- partial_case_count: {partial_count}",
        f"- fail_case_count: {fail_count}",
        f"- infra_fail_case_count: {infra_fail_count}",
    ]
    return "\n".join(lines).strip() + "\n"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Judge one eval case from pure-text evidence")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--profile", required=True, choices=["struct_recovery", "attack_surface", "general_reverse"])
    parser.add_argument("--spec-path", required=True)
    parser.add_argument("--run-trace", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--run-exit-code", type=int, default=0)
    parser.add_argument("--run-status", default="")
    parser.add_argument("--stop-reason", default="")
    args = parser.parse_args(argv)

    verdict_payload = judge_case(
        case_id=str(args.case_id),
        profile=str(args.profile),
        case_spec_text=load_case_spec_text(str(args.spec_path)),
        run_trace_text=load_markdown_artifact(str(args.run_trace)),
        evidence_text=load_markdown_artifact(str(args.evidence)),
        run_exit_code=int(args.run_exit_code),
        run_status=str(args.run_status),
        stop_reason=str(args.stop_reason),
    )
    print(
        build_case_verdict_markdown(
            case_id=str(args.case_id),
            profile=str(args.profile),
            run_exit_code=int(args.run_exit_code),
            run_status=str(args.run_status),
            stop_reason=str(args.stop_reason),
            verdict_payload=verdict_payload,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
