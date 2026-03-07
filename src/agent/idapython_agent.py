"""IDAPython task agent runtime for StructRecoveryRuntimeCore."""
from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from langchain_core.tools import BaseTool, tool

from .idapython_kb import read_file_with_lineno, resolve_kb_root
from .utils import AgentUtils

if TYPE_CHECKING:
    from .struct_recovery_agent import StructRecoveryRuntimeCore


class IDAPythonTaskAgent:
    """Owns the dedicated IDAPython executor-agent workflow."""

    def __init__(self, core: "StructRecoveryRuntimeCore"):
        self.core = core

    def build_executor_tools(
        self,
        *,
        state: Dict[str, Any],
        kb_root: Optional[Path],
        max_iterations: int,
    ) -> List[BaseTool]:
        @tool("submit_idapython_script", parse_docstring=True, error_on_invalid_docstring=True)
        async def submit_idapython_script(script: str, fix_note: str = "") -> str:
            """Submit one script candidate and execute it.

            Args:
                script: 完整 IDAPython 脚本文本。
                fix_note: 本次提交说明（可选）。

            Returns:
                纯文本执行结果。
            """
            if int(state.get("attempts", 0)) >= int(max_iterations):
                return (
                    "ERROR: max_iterations_reached\n"
                    f"- attempts: {int(state.get('attempts', 0))}\n"
                    f"- max_iterations: {int(max_iterations)}"
                )

            candidate = str(script or "").strip()
            if not candidate:
                return "ERROR: missing script"

            destructive_ops = AgentUtils.find_destructive_struct_ops(candidate)
            if destructive_ops:
                return (
                    "ERROR: destructive struct operation is blocked.\n"
                    f"- blocked_operations: {destructive_ops}\n"
                    "- action: use non-destructive updates only"
                )

            state["attempts"] = int(state.get("attempts", 0)) + 1
            state["last_script"] = candidate
            state["last_fix_note"] = str(fix_note or "").strip()

            result = await asyncio.to_thread(
                self.core.ida_client.execute_script,
                candidate,
                state.get("context", {}),
            )
            rendered, is_ok = self.core._render_idapython_execution_output(result)
            state["last_output"] = rendered
            if is_ok:
                state["succeeded"] = True
                state["last_success_output"] = rendered
                return rendered
            state["last_error_output"] = rendered
            return rendered

        @tool("search_ida_symbol", parse_docstring=True, error_on_invalid_docstring=True)
        def search_ida_symbol(query: str, count: int = 20, offset: int = 0) -> str:
            """Search IDA symbols by regex pattern.

            Args:
                query: Python re 正则表达式。
                count: 返回条数（1..100）。
                offset: 结果偏移（>=0）。

            Returns:
                纯文本命中列表。
            """
            lines = [
                "# IDA Symbol Search",
                f"- query: {str(query or '').strip() or '(empty)'}",
                f"- count: {max(1, min(int(count), 100))}",
                f"- offset: {max(0, int(offset))}",
            ]
            text = str(query or "").strip()
            if not text:
                lines.append("ERROR: missing query")
                return "\n".join(lines)
            try:
                payload = self.core.ida_client.search(
                    pattern=text,
                    target_type="symbol",
                    offset=max(0, int(offset)),
                    count=max(1, min(int(count), 100)),
                    flags="IGNORECASE",
                )
            except Exception as e:
                lines.append(f"ERROR: ida symbol search failed: {e}")
                return "\n".join(lines)
            rows = payload.get("items", []) if isinstance(payload, dict) else []
            lines.append(f"- total_count: {int((payload or {}).get('total_count', 0))}")
            lines.append(f"- returned_count: {len(rows)}")
            if not rows:
                lines.append("No matches found.")
                return "\n".join(lines)
            lines.append("")
            lines.append("## Hits")
            for row in rows:
                try:
                    ea_text = f"0x{int(row.get('ea', 0)):x}"
                except Exception:
                    ea_text = "n/a"
                lines.append(f"- {row.get('text', '')} @ {ea_text}")
            return "\n".join(lines)

        @tool("search_kb", parse_docstring=True, error_on_invalid_docstring=True)
        def search_kb(pattern: str, max_hits: int = 120) -> str:
            """Search IDAPython knowledge base with ripgrep.

            Args:
                pattern: ripgrep 正则表达式。
                max_hits: 最多返回命中数（1..300）。

            Returns:
                path:line 命中列表。
            """
            lines = [
                "# KB Search",
                f"- pattern: {str(pattern or '').strip() or '(empty)'}",
                f"- max_hits: {max(1, min(int(max_hits), 300))}",
            ]
            if kb_root is None:
                lines.append("ERROR: search_kb is disabled because idapython kb directory is unavailable")
                return "\n".join(lines)
            text = str(pattern or "").strip()
            if not text:
                lines.append("ERROR: missing pattern")
                return "\n".join(lines)

            cap = max(1, min(int(max_hits), 300))
            try:
                proc = subprocess.run(
                    [
                        "rg",
                        "--line-number",
                        "--no-heading",
                        "--color",
                        "never",
                        "-e",
                        text,
                        str(kb_root),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
            except FileNotFoundError:
                lines.append("ERROR: rg command is not available")
                return "\n".join(lines)
            except subprocess.TimeoutExpired:
                lines.append("ERROR: search_kb timed out")
                return "\n".join(lines)
            except Exception as e:
                lines.append(f"ERROR: search_kb failed: {e}")
                return "\n".join(lines)

            if proc.returncode not in (0, 1):
                lines.append(f"ERROR: rg failed (code={proc.returncode})")
                stderr_text = str(proc.stderr or "").strip()
                if stderr_text:
                    lines.append(f"stderr: {AgentUtils.truncate(stderr_text, 800)}")
                return "\n".join(lines)

            hits: List[str] = []
            for raw in str(proc.stdout or "").splitlines():
                if not raw.strip():
                    continue
                parts = raw.split(":", 2)
                if len(parts) < 2:
                    continue
                path_text = parts[0]
                line_text = parts[1]
                try:
                    rel = Path(path_text).resolve().relative_to(kb_root.resolve()).as_posix()
                except Exception:
                    rel = path_text
                hits.append(f"{rel}:{line_text}")
                if len(hits) >= cap:
                    break

            lines.append(f"- hit_count: {len(hits)}")
            if not hits:
                lines.append("No matches found.")
                return "\n".join(lines)
            lines.append("")
            lines.append("## Hits")
            for row in hits:
                lines.append(f"- {row}")
            return "\n".join(lines)

        @tool("read_file", parse_docstring=True, error_on_invalid_docstring=True)
        def read_file(path: str, line: int) -> str:
            """Read file content around one line from IDAPython knowledge base.

            Args:
                path: 相对知识库根目录的文件路径。
                line: 目标行号（1-based）。

            Returns:
                带行号的纯文本片段。
            """
            if kb_root is None:
                return "ERROR: read_file is disabled because idapython kb directory is unavailable"
            try:
                return read_file_with_lineno(
                    root_dir=kb_root,
                    rel_path=str(path or ""),
                    line=int(line),
                    context_lines=24,
                )
            except Exception as e:
                return f"ERROR: read_file failed: {e}"

        @tool("search_web", parse_docstring=True, error_on_invalid_docstring=True)
        def search_web(query: str) -> str:
            """Search IDAPython related knowledge on the internet.

            Args:
                query: 查询关键词。

            Returns:
                纯文本检索结果或降级提示。
            """
            text = str(query or "").strip()
            if not text:
                return "ERROR: missing query"
            return (
                "INFO: search_web backend is not configured in this deployment.\n"
                "- action: use search_kb/read_file/search_ida_symbol first\n"
                "- note: this tool is reserved for future web backend integration"
            )

        @tool("submit_idapython_result", parse_docstring=True, error_on_invalid_docstring=True)
        def submit_idapython_result(result: str, script: str = "", note: str = "") -> str:
            """Submit final result text for IDAPython task.

            Args:
                result: 最终执行结果文本。
                script: 最终可执行脚本（可选）。
                note: 备注（可选）。

            Returns:
                纯文本确认信息。
            """
            final_result = str(result or "").strip()
            if not final_result:
                final_result = str(state.get("last_success_output", "") or state.get("last_output", "")).strip()
            if not final_result:
                return "ERROR: missing result"
            script_text = str(script or "").strip()
            if script_text:
                state["last_script"] = script_text
            state["last_submit_note"] = str(note or "").strip()
            state["submitted_result"] = final_result
            self.core._finalized = True
            self.core._final_text = final_result
            return "OK: submit_idapython_result accepted"

        tools: List[BaseTool] = [
            search_ida_symbol,
            search_web,
            submit_idapython_script,
            submit_idapython_result,
        ]
        if kb_root is not None:
            tools = [
                search_ida_symbol,
                search_kb,
                read_file,
                search_web,
                submit_idapython_script,
                submit_idapython_result,
            ]
        return tools

    async def run_task(
        self,
        *,
        goal: str,
        background: str,
        max_iterations: int = 8,
    ) -> str:
        goal_text = str(goal or "").strip()
        if not goal_text:
            return "ERROR: missing goal"
        background_text = str(background or "").strip()
        max_iters = max(1, int(max_iterations))
        if int(self.core.agent_depth) + 1 > int(self.core.max_subagent_depth):
            return (
                "ERROR: idapython agent depth limit reached\n"
                f"- current_depth: {int(self.core.agent_depth)}\n"
                f"- max_subagent_depth: {int(self.core.max_subagent_depth)}"
            )

        kb_root = resolve_kb_root(self.core.idapython_kb_dir)
        state: Dict[str, Any] = {
            "attempts": 0,
            "context": {},
            "goal": goal_text,
            "background": background_text,
            "last_script": "",
            "last_output": "",
            "last_error_output": "",
            "last_success_output": "",
            "succeeded": False,
            "submitted_result": "",
        }

        task_lines = [
            "- [ ] 基于 goal/background 输出最小可执行 plan",
            "- [ ] 编写 IDAPython 脚本并调用 submit_idapython_script 执行",
            "- [ ] 若执行失败，仅修改报错相关行并重试",
            "- [ ] 必要时调用 search_ida_symbol/search_kb/read_file/search_web 补充知识",
            "- [ ] 使用 submit_idapython_result 提交最终执行结果",
        ]
        if kb_root is None:
            task_lines[3] = "- [x] 本地知识库不可用，优先 search_ida_symbol + 现有上下文"
        context_md = "\n".join(
            [
                "## Goal",
                AgentUtils.truncate(goal_text, 2400),
                "",
                "## Background",
                AgentUtils.truncate(background_text or "(empty)", 2400),
                "",
                "## Output Policy",
                "- 默认仅返回执行结果正文。",
                "",
                "## Knowledge Base",
                str(kb_root) if kb_root is not None else "(disabled)",
            ]
        )

        child = self.core.subagent_runtime.spawn_isolated_subagent_core()
        child.tool_profile = "idapython_agent"
        child.expert_tools = child.idapython_agent.build_executor_tools(
            state=state,
            kb_root=kb_root,
            max_iterations=max_iters,
        )
        child.expert_tool_map = {row.name: row for row in child.expert_tools}

        subagent_id = f"execpy_{uuid.uuid4().hex[:8]}"
        await child.subagent_runtime.run_subagent_policy_loop(
            user_request="根据 goal/background 编写并执行 IDAPython 脚本，直到得到可复核执行结果或达到最大迭代。",
            request={
                "profile": "idapython_executor" if kb_root is not None else "idapython_executor_no_kb",
                "priority": "high",
                "task": "\n".join(task_lines),
                "context": context_md,
            },
            max_iterations=max_iters,
            agent_id=subagent_id,
            parent_agent_id="main",
        )

        if str(state.get("submitted_result", "")).strip():
            return str(state.get("submitted_result", "")).strip()
        if state.get("succeeded") and str(state.get("last_success_output", "")).strip():
            return str(state.get("last_success_output", "")).strip()

        last_error = str(state.get("last_error_output", "") or state.get("last_output", "")).strip()
        if not last_error:
            return (
                "ERROR: run_idapython_task failed and no execution output captured.\n"
                f"- attempts: {int(state.get('attempts', 0))}\n"
                f"- max_iterations: {max_iters}"
            )
        if "max_iterations_reached" not in last_error:
            last_error += f"\nERROR: max_iterations_reached ({max_iters})"
        return last_error
