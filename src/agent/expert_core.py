"""Reverse Expert Agent Core - LLM-driven single policy loop with minimal runtime kernel."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI

from .context_distiller import ContextDistillerAgent
from .context_manager import ContextManager
from .ida_client import IDAClient
from .idapython_kb import read_file_with_lineno, resolve_kb_root, search_regex
from .knowledge_manager import KnowledgeManager
from .models import ContextMessageRow, PolicyMessageRef, SubAgentState, WorkingKnowledge
from .observability import ObservabilityHub
from .policy_manager import PolicyManager
from .prompt_manager import PromptManager
from .session_logger import AgentSessionLogger
from .subagent_manager import SubAgentManager
from .task_board import TaskBoard
from .tools import full_tools as all_registered_tools, set_ida_client, tools as registered_tools
from .utils import AgentUtils


class ReverseExpertAgentCore:
    """LLM-driven reverse analysis expert with a single tool-call policy loop."""

    REQUIRED_MODEL = "gpt-5.2"

    def __init__(
        self,
        ida_service_url: str = "http://127.0.0.1:5000",
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        model: Optional[str] = None,
        prompt_root: Optional[str] = None,
        agent_depth: int = 0,
        idapython_kb_dir: str = "",
    ):
        self.ida_client = IDAClient(base_url=ida_service_url)
        set_ida_client(self.ida_client)

        api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not provided")

        base_url = openai_base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model_name = str(model or os.getenv("OPENAI_MODEL", self.REQUIRED_MODEL)).strip()
        if model_name != self.REQUIRED_MODEL:
            raise ValueError(
                f"Unsupported model '{model_name}'. "
                f"Only '{self.REQUIRED_MODEL}' is allowed for this project."
            )

        self.model = model_name
        self._openai_api_key = api_key
        self._openai_base_url = base_url
        self._prompt_root = prompt_root
        self.idapython_kb_dir = str(idapython_kb_dir or os.getenv("IDAPYTHON_KB_DIR", "")).strip()
        self.agent_depth = max(0, int(agent_depth))
        self.llm = ChatOpenAI(
            model=model_name,
            temperature=0,
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
        )

        self.enable_session_log = True
        self.session_log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "logs",
            "agent_sessions",
        )

        self.tool_result_log_chars = 4000
        self.message_log_chars = 12000
        self.llm_max_retries = 1
        self.subagent_max_retries = 0
        self.tool_call_max_parallel = 4
        self.tool_call_collect_all_errors = True
        self.tool_profile = "struct_recovery"
        self.max_subagent_depth = 2
        self.subagent_default_max_iterations = 8
        self.expert_tools = self._build_expert_tools(self.tool_profile)
        self.expert_tool_map = {row.name: row for row in self.expert_tools}

        self.context_window_messages = 120
        self.subagent_max_context_chars = 3200
        self.subagent_max_parallel = 4
        self.policy_history_max_messages = 220
        self.policy_history_max_chars = 260000
        self.policy_history_soft_ratio = 0.82
        self.policy_history_keep_tail_messages = 120
        self.policy_history_distill_max_messages = 200
        self.context_fold_placeholder = "内容已经折叠，信息如需要请重新获取。"
        self.prompt_manager = PromptManager(prompt_root=prompt_root)
        self.prompt_manager.validate_required(
            [
                "agent/reverse_expert_system.md",
                "agent/subagent_user.md",
                "agent/precompression_notice.md",
                "agent/policy_compress_snapshot.md",
                "distiller/system.md",
                "distiller/user.md",
                "subagents/general.md",
                "subagents/document_agent.md",
                "subagents/idapython_executor.md",
                "subagents/idapython_executor_no_kb.md",
                "subagents/fragments/idapython_template.md",
                "subagents/fragments/ida93_banned_apis.md",
                "subagents/fragments/idapython_examples.md",
            ]
        )
        self.context_distiller = ContextDistillerAgent(self.llm, self.prompt_manager)

        self.session_logger: Optional[AgentSessionLogger] = None
        self.obs = ObservabilityHub(None, debug_enabled=self._debug_enabled())
        self.last_session_id: Optional[str] = None
        self.session_db_path: Optional[str] = None

        self.policy_id = "llm_driven_v1"
        self.loop_mode = "single_policy_loop"
        self.git_commit = AgentUtils.git_commit()

        # Initialize managers
        self.knowledge_mgr = KnowledgeManager()
        self.policy_mgr = PolicyManager(context_fold_placeholder=self.context_fold_placeholder)
        self.context_mgr = ContextManager(context_window_messages=self.context_window_messages)
        self.subagent_mgr = SubAgentManager(obs=self.obs)
        self.task_board = TaskBoard(agent_id="main")

        self._subagent_sem: Optional[asyncio.Semaphore] = None
        self._context_compress_requested: bool = False
        self._context_compress_reason: str = ""
        self._precompression_notice_pending: bool = False
        self._precompression_notice_iteration: int = 0
        self._task_board_bootstrap_emitted: bool = False

        self._finalized: bool = False
        self._final_text: str = ""
        self._finalize_payload: Dict[str, str] = {}
        self._effective_mutation_count: int = 0
        self._effective_type_application_count: int = 0


    def _debug_enabled(self) -> bool:
        value = str(os.getenv("AGENT_DEBUG", os.getenv("AGENT_DEBUG_TRACE", "0"))).strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _init_session_logger(self) -> None:
        if not self.enable_session_log:
            self.session_logger = None
            self.last_session_id = None
            self.session_db_path = None
            self.obs = ObservabilityHub(None, debug_enabled=self._debug_enabled())
            return

        self.session_logger = AgentSessionLogger(self.session_log_dir)
        self.last_session_id = self.session_logger.session_id
        self.session_db_path = self.session_logger.db_path
        self.obs = ObservabilityHub(self.session_logger, debug_enabled=self._debug_enabled())


    def _render_idapython_execution_output(self, result: Dict[str, Any]) -> tuple[str, bool]:
        success = bool(result.get("success", False))
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or result.get("error") or "")
        execution_time = float(result.get("execution_time", 0) or 0)
        runtime_error = AgentUtils.has_runtime_error_marker(stdout) or AgentUtils.has_runtime_error_marker(stderr)
        if success and (not runtime_error):
            lines = [
                "OK: execute_idapython",
                f"Execution time: {execution_time:.3f}s",
            ]
            if result.get("result") is not None:
                lines.append(f"Result: {AgentUtils.truncate(str(result.get('result')), 2400)}")
            if stdout.strip():
                lines.append(f"Stdout:\n{AgentUtils.truncate(stdout, 3200)}")
            return "\n".join(lines), True

        lines = [
            "ERROR: execute_idapython failed",
            f"Execution time: {execution_time:.3f}s",
        ]
        if stderr.strip():
            lines.append(f"Stderr:\n{AgentUtils.truncate(stderr, 3200)}")
        elif stdout.strip():
            lines.append(f"Stdout:\n{AgentUtils.truncate(stdout, 3200)}")
        else:
            lines.append("Stderr:\nunknown runtime error")
        return "\n".join(lines), False

    def _resolve_idapython_kb_root(self) -> Optional[Path]:
        return resolve_kb_root(self.idapython_kb_dir)

    def _make_execute_idapython_tool(self) -> BaseTool:
        @tool("execute_idapython", parse_docstring=True, error_on_invalid_docstring=True)
        async def execute_idapython(script: str, context: Optional[Dict[str, Any]] = None) -> str:
            """Execute IDAPython with an internal repair agent loop.

            功能:
                先执行脚本；失败后把报错返回给 LLM，进入最多 8 轮最小修复重试，直到成功或达到上限。
            适用场景:
                结构化工具不足，需要灵活脚本采证或批处理动作。
            不适用场景:
                已有结构化工具能直接完成目标时。
            示例:
                execute_idapython(script="import idc\\n__result__ = idc.get_func_name(0x140001000)")
            返回值语义:
                OK: 返回最后一次成功执行输出。
                ERROR: 返回最后一次失败信息或达到迭代上限。

            Args:
                script: 待执行的 IDAPython 脚本文本。
                context: 可选上下文变量，注入脚本命名空间。

            Returns:
                纯文本执行结果。
            """
            return await self._run_execute_idapython_agent(
                script=script,
                context=context,
                max_iterations=8,
            )

        return execute_idapython

    @staticmethod
    def _replace_execute_idapython_tool(tools: List[Any], execute_tool: BaseTool) -> List[Any]:
        replaced: List[Any] = []
        swapped = False
        for tool_obj in tools:
            if str(getattr(tool_obj, "name", "") or "") == "execute_idapython":
                if not swapped:
                    replaced.append(execute_tool)
                    swapped = True
                continue
            replaced.append(tool_obj)
        if not swapped:
            replaced.insert(0, execute_tool)
        return replaced

    def _build_expert_tools(self, profile: str) -> List[Any]:
        execute_tool = self._make_execute_idapython_tool()
        if profile in {"minimal_codeact", "execute_only", "codeact"}:
            return [execute_tool]
        if profile in {"struct_recovery"}:
            return self._replace_execute_idapython_tool(list(registered_tools), execute_tool)
        if profile in {"full_tools", "full"}:
            return self._replace_execute_idapython_tool(list(all_registered_tools), execute_tool)
        return [execute_tool]

    def _reset_runtime_state(self) -> None:
        self.knowledge_mgr.reset()
        self.policy_mgr.reset()
        self.context_mgr.reset()
        self.subagent_mgr.reset()
        self.task_board.reset()
        self.task_board.set_on_change(None)
        self._subagent_sem = asyncio.Semaphore(self.subagent_max_parallel)
        self._context_compress_requested = False
        self._context_compress_reason = ""
        self._precompression_notice_pending = False
        self._precompression_notice_iteration = 0
        self._task_board_bootstrap_emitted = False
        self._finalized = False
        self._final_text = ""
        self._finalize_payload = {}
        self._effective_mutation_count = 0
        self._effective_type_application_count = 0

    def _append_context_message(
        self,
        *,
        role: str,
        source: str,
        content: str,
        turn_id: str,
        agent_id: str,
        pinned: bool = False,
    ) -> ContextMessageRow:
        self._context_seq += 1
        row = ContextMessageRow(
            message_id=f"Message_{self._context_seq:06d}",
            role=str(role or "assistant"),
            source=str(source or "runtime"),
            content=str(content or ""),
            turn_id=str(turn_id or ""),
            agent_id=str(agent_id or "main"),
            created_at=time.time(),
            pinned=bool(pinned),
            pruned=False,
            folded=False,
        )
        self.context_mgr._context_messages.append(row)
        return row

    def _next_policy_message_id(self) -> str:
        self._policy_seq += 1
        return f"Message_{self._policy_seq:06d}"

    def _inject_message_id_text(self, content: Any, message_id: str) -> str:
        prefix = f"消息ID: {str(message_id or '').strip()}"
        text = AgentUtils.content_to_text(content)
        if text.startswith(prefix):
            return text
        lines = text.splitlines()
        if lines and re.match(r"^\s*消息ID:\s*Message_\d+\s*$", lines[0].strip()):
            text = "\n".join(lines[1:])
        body = str(text or "").lstrip("\n")
        if body:
            return f"{prefix}\n{body}"
        return prefix

    def _append_policy_message(
        self,
        *,
        messages: List[Any],
        message_obj: Any,
        role: str,
        turn_id: str,
        protected: bool = False,
    ) -> Any:
        message_id = self.policy_mgr.next_message_id()
        content_text = self.policy_mgr.inject_message_id_text(getattr(message_obj, "content", ""), message_id)
        if isinstance(message_obj, SystemMessage):
            final_obj: Any = SystemMessage(content=content_text)
        elif isinstance(message_obj, HumanMessage):
            final_obj = HumanMessage(content=content_text)
        elif isinstance(message_obj, AIMessage):
            final_obj = AIMessage(content=content_text, tool_calls=list(getattr(message_obj, "tool_calls", None) or []))
        elif isinstance(message_obj, ToolMessage):
            final_obj = ToolMessage(content=content_text, tool_call_id=str(getattr(message_obj, "tool_call_id", "") or ""))
        else:
            final_obj = HumanMessage(content=content_text)
        messages.append(final_obj)
        ref = PolicyMessageRef(
            message_id=message_id,
            role=str(role or "user"),
            turn_id=str(turn_id or ""),
            created_at=time.time(),
            message_obj=final_obj,
            protected=bool(protected),
            folded=False,
            active=True,
        )
        self.policy_mgr._policy_messages_by_id[message_id] = ref
        self.policy_mgr._policy_message_order.append(message_id)
        self.policy_mgr._policy_message_obj_to_id[id(final_obj)] = message_id
        return final_obj

    def _message_id_of_obj(self, message_obj: Any) -> str:
        if message_obj is None:
            return ""
        mapped = self.policy_mgr._policy_message_obj_to_id.get(id(message_obj), "")
        if mapped:
            return mapped
        content_text = AgentUtils.content_to_text(getattr(message_obj, "content", ""))
        picked = PolicyManager._extract_message_ids(content_text)
        return picked[0] if picked else ""

    def _refresh_policy_message_active_flags(self, messages: List[Any]) -> None:
        active_ids = set()
        for msg in messages:
            mid = self.policy_mgr.message_id_of_obj(msg)
            if mid:
                active_ids.add(mid)
        for message_id, ref in self.policy_mgr._policy_messages_by_id.items():
            ref.active = message_id in active_ids

    def _fold_policy_message(self, message_id: str, reason: str = "") -> str:
        ref = self.policy_mgr._policy_messages_by_id.get(str(message_id or "").strip())
        if not ref:
            return "unmatched"
        if not ref.active:
            return "unmatched"
        if ref.protected:
            return "protected"
        if ref.folded:
            return "already_folded"
        try:
            ref.message_obj.content = self.policy_mgr.inject_message_id_text(self.context_fold_placeholder, ref.message_id)
            if isinstance(ref.message_obj, AIMessage):
                ref.message_obj.tool_calls = []
            ref.folded = True
            return "folded"
        except Exception:
            return "unmatched"

    def _active_policy_refs(self) -> List[PolicyMessageRef]:
        rows: List[PolicyMessageRef] = []
        for mid in self.policy_mgr._policy_message_order:
            ref = self.policy_mgr._policy_messages_by_id.get(mid)
            if not ref:
                continue
            if not ref.active:
                continue
            rows.append(ref)
        return rows

    def _pending_subagents_for_parent(self, *, parent_agent_id: str) -> List[SubAgentState]:
        parent_id = str(parent_agent_id or "").strip()
        if not parent_id:
            return []
        rows: List[SubAgentState] = []
        for state in self.subagent_mgr._subagents.values():
            if state.parent_agent_id != parent_id:
                continue
            if state.status == "running":
                rows.append(state)
        return rows

    def _drain_completed_subagent_updates(self, *, parent_agent_id: str) -> str:
        parent_id = str(parent_agent_id or "").strip()
        if not parent_id:
            return ""
        delivered: List[SubAgentState] = []
        for state in self.subagent_mgr._subagents.values():
            if state.parent_agent_id != parent_id:
                continue
            if state.status == "running":
                continue
            if state.delivered_to_parent:
                continue
            state.delivered_to_parent = True
            state.updated_at = time.time()
            delivered.append(state)
            self.obs.emit(
                "subagent_result_delivered",
                {
                    "agent_id": state.agent_id,
                    "parent_agent_id": state.parent_agent_id,
                    "status": state.status,
                    "profile": state.profile,
                    "priority": state.priority,
                    "task_md": AgentUtils.truncate(state.task_md, 800),
                    "result_preview": AgentUtils.truncate(state.result_md, 1200),
                    "policy_id": self.policy_id,
                    "git_commit": self.git_commit,
                    "loop_mode": self.loop_mode,
                },
            )

        if not delivered:
            return ""

        lines: List[str] = [
            "# Subagent Result Updates",
            f"- parent_agent_id: {parent_id}",
            f"- delivered_count: {len(delivered)}",
            "",
        ]
        for state in delivered:
            lines.extend(
                [
                    f"## {state.agent_id}",
                    f"- status: {state.status}",
                    f"- profile: {state.profile}",
                    f"- priority: {state.priority}",
                    f"- task: {AgentUtils.truncate(state.task_md, 600)}",
                    "- result:",
                    AgentUtils.truncate(state.result_md or "(empty subagent result)", 3200),
                    "",
                ]
            )
        return "\n".join(lines).strip()

    def _is_protected_policy_obj(self, message_obj: Any) -> bool:
        message_id = self.policy_mgr.message_id_of_obj(message_obj)
        if not message_id:
            return False
        ref = self.policy_mgr._policy_messages_by_id.get(message_id)
        return bool(ref and ref.protected)

    def _context_rows(self, *, include_pruned: bool = False, include_folded: bool = True) -> List[ContextMessageRow]:
        rows = list(self.context_mgr._context_messages) if include_pruned else [row for row in self.context_mgr._context_messages if not row.pruned]
        if include_folded:
            return rows
        return [row for row in rows if not row.folded]

    def _context_markdown(
        self,
        *,
        max_messages: Optional[int] = None,
        include_pruned: bool = False,
        include_folded: bool = True,
    ) -> str:
        rows = self.context_mgr.get_rows(include_pruned=include_pruned, include_folded=include_folded)
        if not rows:
            return "(empty)"
        if max_messages is None:
            max_messages = self.context_window_messages
        tail = rows[-max(1, int(max_messages)) :]
        lines: List[str] = []
        for row in tail:
            state: List[str] = ["pruned" if row.pruned else "active"]
            if row.folded:
                state.append("folded")
            if row.pinned:
                state.append("pinned")
            lines.append(f"消息ID: {row.message_id}")
            lines.append(f"- role: {row.role}")
            lines.append(f"- state: {','.join(state)}")
            lines.append(f"- source: {row.source}")
            lines.append(f"- turn_id: {row.turn_id}")
            content_text = self.context_fold_placeholder if row.folded else row.content
            lines.append(f"- content: {AgentUtils.truncate(content_text, 380)}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _extract_message_ids(text: str) -> List[str]:
        picked: List[str] = []
        seen = set()
        for token in re.findall(r"(Message_\d+|m\d{4,6})", str(text or ""), flags=re.IGNORECASE):
            mid = str(token).strip()
            if not mid:
                continue
            if mid.lower().startswith("m") and (not mid.startswith("Message_")):
                try:
                    seq = int(mid[1:])
                    mid = f"Message_{seq:06d}"
                except Exception:
                    pass
            if mid in seen:
                continue
            seen.add(mid)
            picked.append(mid)
        return picked

    def _attach_task_board_bootstrap_if_needed(self, result: str) -> str:
        text = str(result or "")
        if self._task_board_bootstrap_emitted:
            return text
        if not text.startswith("OK:"):
            return text
        if self.task_board.task_count() <= 0:
            return text

        self._task_board_bootstrap_emitted = True
        return (
            text
            + "\n\n## Task Board (Initial Snapshot)\n"
            + self.task_board.get_task_board(view="both")
            + '\n\n提示：任务板后续不会自动注入；需要时调用 `get_task_board(view="both")`。'
        )

    def _knowledge_markdown(self, max_items: int = 20) -> str:
        cap = max(1, int(max_items))

        def _section(title: str, items: List[str]) -> List[str]:
            lines = [f"### {title}"]
            if not items:
                lines.append("- (none)")
                return lines
            for item in items[:cap]:
                lines.append(f"- {AgentUtils.truncate(item, 260)}")
            if len(items) > cap:
                lines.append(f"- ... and {len(items) - cap} more")
            return lines

        blocks: List[str] = []
        blocks.extend(_section("Confirmed Facts", self.knowledge_mgr.knowledge.confirmed_facts))
        blocks.extend(_section("Hypotheses", self.knowledge_mgr.knowledge.hypotheses))
        blocks.extend(_section("Open Questions", self.knowledge_mgr.knowledge.open_questions))
        blocks.extend(_section("Evidence", self.knowledge_mgr.knowledge.evidence))
        blocks.extend(_section("Next Actions", self.knowledge_mgr.knowledge.next_actions))
        blocks.extend(_section("Do Not Repeat", self.knowledge_mgr.knowledge.do_not_repeat))
        return "\n".join(blocks)

    @staticmethod
    def _clean_lines(text: str) -> List[str]:
        out: List[str] = []
        seen = set()
        for line in str(text or "").splitlines():
            row = line.strip()
            if not row:
                continue
            if row.startswith("- "):
                row = row[2:].strip()
            if row in seen:
                continue
            seen.add(row)
            out.append(row)
        return out

    def _update_knowledge(self, *, section: str, values: List[str], overwrite: bool = False, source: str = "runtime") -> None:
        target = getattr(self._working_knowledge, section, None)
        if not isinstance(target, list):
            return
        cleaned: List[str] = []
        seen = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            cleaned.append(text)
            seen.add(text)
        if not cleaned:
            return

        if overwrite:
            target[:] = cleaned
        else:
            for text in cleaned:
                if text not in target:
                    target.append(text)

        self.obs.emit(
            "knowledge_updated",
            {
                "section": section,
                "overwrite": bool(overwrite),
                "added_count": len(cleaned),
                "source": source,
                "policy_id": self.policy_id,
                "git_commit": self.git_commit,
                "loop_mode": self.loop_mode,
            },
        )

    @staticmethod
    def _is_retryable_llm_error(error_text: str) -> bool:
        text = str(error_text).lower()
        return (
            "429" in text
            or "rate limit" in text
            or "temporarily unavailable" in text
            or "service unavailable" in text
            or "timeout" in text
            or "try again" in text
        )

    async def _ainvoke_with_retry(self, runnable: Any, messages: List[Any], max_retries: int) -> Any:
        attempt = 0
        while True:
            try:
                return await runnable.ainvoke(messages)
            except Exception as e:
                if attempt >= int(max_retries) or (not self._is_retryable_llm_error(str(e))):
                    raise
                await asyncio.sleep(min(12, 2 * (attempt + 1)))
                attempt += 1

    @staticmethod
    def _parse_mutation_effective(result: str) -> Optional[bool]:
        text = str(result or "")
        match = re.search(
            r"[\"']?mutation_effective[\"']?\s*[:=]\s*[\"']?(true|false|1|0)[\"']?",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = match.group(1).strip().lower()
        return value in {"true", "1"}

    @staticmethod
    def _is_mutating_tool(tool_name: str) -> bool:
        return tool_name in {
            "create_structure",
            "set_identifier_type",
        }

    @staticmethod
    def _is_type_application_tool(tool_name: str) -> bool:
        return tool_name in {"set_identifier_type"}

    def _normalize_tool_calls(self, raw_tool_calls: Any, *, turn_id: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        source = raw_tool_calls if isinstance(raw_tool_calls, list) else []
        for idx, tc in enumerate(source, start=1):
            if isinstance(tc, dict):
                tool_name = str(tc.get("name", "") or "")
                tool_args = tc.get("args", {})
                tool_call_id = str(tc.get("id", "") or "")
            else:
                tool_name = str(getattr(tc, "name", "") or getattr(tc, "tool_name", "") or "")
                tool_args = getattr(tc, "args", {}) or getattr(tc, "arguments", {})
                tool_call_id = str(getattr(tc, "id", "") or "")
            if not isinstance(tool_args, dict):
                tool_args = {"raw": tool_args}
            if not tool_call_id:
                tool_call_id = f"{turn_id}:tool:{idx}"
            rows.append(
                {
                    "id": tool_call_id,
                    "name": tool_name,
                    "args": tool_args,
                }
            )
        return rows

    def _serialize_messages_for_log(self, messages: List[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for msg in messages:
            msg_type = str(getattr(msg, "type", "") or "").strip().lower()
            if msg_type == "system":
                role = "system"
            elif msg_type == "ai":
                role = "assistant"
            elif msg_type == "tool":
                role = "tool"
            else:
                role = "user"
            content_text = AgentUtils.content_to_text(getattr(msg, "content", ""))
            row: Dict[str, Any] = {
                "role": role,
                "content": AgentUtils.truncate(content_text, self.message_log_chars),
            }
            if role == "assistant":
                tool_calls = getattr(msg, "tool_calls", None)
                if isinstance(tool_calls, list) and tool_calls:
                    # Normalize tool_calls to plain dicts for JSON serialization
                    normalized = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            normalized.append({
                                "id": str(tc.get("id", "") or ""),
                                "name": str(tc.get("name", "") or ""),
                                "args": tc.get("args", {}),
                            })
                        else:
                            # Handle LangChain ToolCall objects
                            normalized.append({
                                "id": str(getattr(tc, "id", "") or ""),
                                "name": str(getattr(tc, "name", "") or getattr(tc, "tool_name", "") or ""),
                                "args": getattr(tc, "args", {}) or getattr(tc, "arguments", {}) or {},
                            })
                    row["tool_calls"] = normalized
            if role == "tool":
                row["tool_call_id"] = str(getattr(msg, "tool_call_id", "") or "")
                row["is_error"] = content_text.startswith("ERROR:") or content_text.startswith("Error:") or content_text.startswith("[ERROR]")
            rows.append(row)
        return rows

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        value = str(text or "")
        if not value:
            return 0
        return max(1, len(value) // 4)

    def _policy_history_usage(self, messages: List[Any]) -> Dict[str, int]:
        total_chars = 0
        total_tokens = 0
        for msg in messages:
            body = AgentUtils.content_to_text(getattr(msg, "content", ""))
            total_chars += len(body)
            total_tokens += PolicyManager.estimate_tokens(body)
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                tc_text = AgentUtils.content_to_text(tool_calls)
                total_chars += len(tc_text)
                total_tokens += PolicyManager.estimate_tokens(tc_text)
        return {
            "message_count": len(messages),
            "total_chars": total_chars,
            "total_tokens": total_tokens,
        }

    def _render_policy_messages_for_distill(self, messages: List[Any]) -> str:
        if not messages:
            return "(empty)"
        cap = max(20, int(self.policy_history_distill_max_messages))
        picked = messages[-cap:]
        lines: List[str] = []
        start = max(0, len(messages) - len(picked))
        for idx, msg in enumerate(picked, start=start + 1):
            msg_type = str(getattr(msg, "type", "") or "").strip().lower()
            if msg_type == "system":
                role = "system"
            elif msg_type == "ai":
                role = "assistant"
            elif msg_type == "tool":
                role = "tool"
            else:
                role = "user"
            message_id = self.policy_mgr.message_id_of_obj(msg) or f"Message_{idx:06d}"
            lines.append(f"消息ID: {message_id}")
            lines.append(f"- role: {role}")
            lines.append(f"- content: {AgentUtils.truncate(AgentUtils.content_to_text(getattr(msg, 'content', '')), 1200)}")
            tool_calls = getattr(msg, "tool_calls", None)
            if isinstance(tool_calls, list) and tool_calls:
                lines.append(f"- tool_calls: {AgentUtils.truncate(AgentUtils.content_to_text(tool_calls), 800)}")
            if role == "tool":
                lines.append(f"- tool_call_id: {str(getattr(msg, 'tool_call_id', '') or '')}")
            lines.append("")
        return "\n".join(lines)

    def _build_policy_compress_snapshot(self, *, iteration: int, user_request: str) -> str:
        return self.prompt_manager.render(
            "agent/policy_compress_snapshot.md",
            {
                "user_request": str(user_request or ""),
                "iteration": int(iteration),
                "key_technical_concepts": self.knowledge_mgr.to_markdown(max_items=8),
                "pending_tasks": self.task_board.render_status_board(),
                "current_work": self.context_mgr.to_markdown(max_messages=30),
                "optional_next_step": "\n".join([f"- {x}" for x in self.knowledge_mgr.knowledge.next_actions[:10]]) or "- 无",
                "handoff_anchors": "\n".join([f"- {x}" for x in self.knowledge_mgr.knowledge.evidence[:10]]) or "- 无",
            },
        )

    def _build_precompression_notice(self, *, iteration: int, usage: Dict[str, int], reason: str) -> str:
        rows = self.policy_mgr.active_refs()
        lines: List[str] = []
        if not rows:
            lines.append("(empty context)")
        else:
            for row in rows[-120:]:
                state_parts = ["folded" if row.folded else "active"]
                if row.protected:
                    state_parts.append("protected")
                lines.append(f"- {row.message_id} [{row.role}] state={','.join(state_parts)} source=policy")
                content_text = AgentUtils.content_to_text(getattr(row.message_obj, "content", ""))
                lines.append(f"  {AgentUtils.truncate(content_text, 180)}")
        return self.prompt_manager.render(
            "agent/precompression_notice.md",
            {
                "iteration": int(iteration),
                "reason": str(reason or ""),
                "policy_messages": int(usage.get("message_count", 0)),
                "policy_tokens_est": int(usage.get("total_tokens", 0)),
                "prunable_rows_md": "\n".join(lines),
            },
        )

    async def _distill_and_compress_policy_history(
        self,
        *,
        messages: List[Any],
        iteration: int,
        user_request: str,
        agent_id: str,
        parent_agent_id: str,
        turn_id: str,
        reason: str,
    ) -> None:
        usage_before = self.policy_mgr.calculate_usage(messages)
        original_count = len(messages)
        keep_tail = max(20, int(self.policy_history_keep_tail_messages))
        tail_start = max(0, len(messages) - keep_tail)
        protected_messages = [msg for msg in messages if self._is_protected_policy_obj(msg)]

        compress_candidates: List[Any] = []
        for idx, msg in enumerate(messages):
            if self._is_protected_policy_obj(msg):
                continue
            if idx >= tail_start:
                continue
            compress_candidates.append(msg)
        if not compress_candidates:
            return

        history_md = self._render_policy_messages_for_distill(compress_candidates)
        distilled_summary = ""
        do_not_repeat: List[str] = []
        next_actions: List[str] = []
        try:
            distilled = await self.context_distiller.distill(
                user_request=user_request,
                iteration=iteration,
                task_board_md=self.task_board.get_task_board(view="both"),
                knowledge_md=self.knowledge_mgr.to_markdown(max_items=20),
                context_md=self.context_mgr.to_markdown(max_messages=60),
                history_md=history_md,
            )
            distilled_summary = str(distilled.summary_markdown or "").strip()
            do_not_repeat = list(distilled.do_not_repeat or [])
            next_actions = list(distilled.next_actions or [])
            if distilled.confirmed_facts:
                self.knowledge_mgr.update(
                    section="confirmed_facts",
                    values=list(distilled.confirmed_facts),
                    overwrite=False,
                    source="context_distiller",
                )
            if distilled.evidence:
                self.knowledge_mgr.update(
                    section="evidence",
                    values=list(distilled.evidence),
                    overwrite=False,
                    source="context_distiller",
                )
        except Exception:
            distilled_summary = ""

        if do_not_repeat:
            self.knowledge_mgr.update(
                section="do_not_repeat",
                values=do_not_repeat,
                overwrite=False,
                source="context_distiller",
            )
        if next_actions:
            self.knowledge_mgr.update(
                section="next_actions",
                values=next_actions,
                overwrite=False,
                source="context_distiller",
            )

        if not distilled_summary:
            distilled_summary = self._build_policy_compress_snapshot(iteration=iteration, user_request=user_request)

        rebuilt: List[Any] = list(protected_messages)
        self.policy_mgr.append_message(
            messages=rebuilt,
            message_obj=HumanMessage(content=distilled_summary),
            role="user",
            turn_id=turn_id,
            protected=False,
        )
        for msg in messages[tail_start:]:
            if any(msg is row for row in protected_messages):
                continue
            rebuilt.append(msg)
        messages[:] = rebuilt
        self.policy_mgr.refresh_active_flags(messages)

        usage_after = self.policy_mgr.calculate_usage(messages)
        self.obs.emit(
            "context_compressed",
            {
                "turn_id": turn_id,
                "agent_id": agent_id,
                "parent_agent_id": parent_agent_id,
                "removed_count": max(0, original_count - len(messages)),
                "reason": str(reason or "policy_history_overflow"),
                "compression_mode": "llm_8block",
                "before_messages": int(usage_before.get("message_count", 0)),
                "after_messages": int(usage_after.get("message_count", 0)),
                "before_chars": int(usage_before.get("total_chars", 0)),
                "after_chars": int(usage_after.get("total_chars", 0)),
                "before_tokens_est": int(usage_before.get("total_tokens", 0)),
                "after_tokens_est": int(usage_after.get("total_tokens", 0)),
                "do_not_repeat": list(self.knowledge_mgr.knowledge.do_not_repeat[-12:]),
                "next_actions": list(self.knowledge_mgr.knowledge.next_actions[-12:]),
                "policy_id": self.policy_id,
                "git_commit": self.git_commit,
                "loop_mode": self.loop_mode,
            },
        )

    async def _maybe_compress_policy_history(
        self,
        *,
        messages: List[Any],
        iteration: int,
        user_request: str,
        agent_id: str,
        parent_agent_id: str,
        turn_id: str,
    ) -> str:
        usage = self.policy_mgr.calculate_usage(messages)
        max_messages = max(10, int(self.policy_history_max_messages))
        max_chars = max(40000, int(self.policy_history_max_chars))
        soft_messages = max(8, int(max_messages * float(self.policy_history_soft_ratio)))
        soft_chars = max(32000, int(max_chars * float(self.policy_history_soft_ratio)))

        over_soft = (
            int(usage.get("message_count", 0)) > soft_messages
            or int(usage.get("total_chars", 0)) > soft_chars
            or self._context_compress_requested
        )
        over_hard = (
            int(usage.get("message_count", 0)) > max_messages
            or int(usage.get("total_chars", 0)) > max_chars
            or self._context_compress_requested
        )
        if not over_soft:
            self._precompression_notice_pending = False
            return ""

        if (not over_hard) and (not self._precompression_notice_pending):
            self._precompression_notice_pending = True
            self._precompression_notice_iteration = int(iteration)
            return self._build_precompression_notice(
                iteration=iteration,
                usage=usage,
                reason="policy_history_soft_threshold",
            )
        if (not over_hard) and self._precompression_notice_pending and int(iteration) <= int(self._precompression_notice_iteration) + 1:
            return ""

        reason = self._context_compress_reason or ("policy_history_overflow" if over_hard else "policy_history_soft_unresolved")
        self._context_compress_requested = False
        self._context_compress_reason = ""
        self._precompression_notice_pending = False
        await self._distill_and_compress_policy_history(
            messages=messages,
            iteration=iteration,
            user_request=user_request,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            turn_id=turn_id,
            reason=reason,
        )
        return ""

    def _bind_task_board_observer(self, *, current_agent_id: str, parent_agent_id: str) -> None:
        def _on_change(changed_task_ids: List[str]) -> None:
            self.obs.emit(
                "task_board_updated",
                {
                    "agent_id": current_agent_id,
                    "parent_agent_id": parent_agent_id,
                    "changed_task_ids": list(changed_task_ids),
                    "task_count": int(self.task_board.task_count()),
                    "policy_id": self.policy_id,
                    "git_commit": self.git_commit,
                    "loop_mode": self.loop_mode,
                },
            )

        self.task_board.set_on_change(_on_change)

    def _make_runtime_tools(
        self,
        *,
        current_agent_id: str,
        parent_agent_id: str,
        user_request: str,
        max_iterations: int,
        include_context_tools: bool = True,
        finalize_mode: str = "main",
    ) -> List[BaseTool]:
        self._bind_task_board_observer(current_agent_id=current_agent_id, parent_agent_id=parent_agent_id)

        @tool("create_task", parse_docstring=True, error_on_invalid_docstring=True)
        def create_task(
            title: str = "",
            details: str = "",
            priority: str = "normal",
            tasks: Optional[List[Dict[str, Any]]] = None,
        ) -> str:
            """Create one task or multiple tasks in the markdown task board.

            功能:
                创建可追踪任务条目，供后续状态推进与闭环验收。
            适用场景:
                目标已明确，需要拆解为可执行子任务。
            不适用场景:
                仅有模糊方向、尚未形成可执行动作。
            示例:
                create_task(title="收集 foo 函数结构体访问证据", details="- 先反编译再偏移采证", priority="high")
                create_task(tasks=[{"title":"定位入口函数","priority":"high"},{"title":"采集偏移证据","details":"- main/sub_1780","priority":"high"}])
            返回值语义:
                OK: 返回新任务标识与摘要。
                ERROR: 参数或任务板状态不满足创建条件。

            Args:
                title: 单任务标题，建议单句且可验证。
                details: 单任务细节，使用纯文本或 Markdown 列表。
                priority: 单任务优先级，常用值 high/normal/low。
                tasks: 批量任务对象数组，每项包含 title/details/priority。

            Returns:
                纯文本结果，前缀为 OK: 或 ERROR:。
            """
            if tasks is not None:
                if str(title or "").strip():
                    return "ERROR: provide either 'title' or 'tasks'"
                if str(details or "").strip():
                    return "ERROR: 'details' is not used with 'tasks'; put details inside each task object"
                if str(priority or "normal").strip().lower() not in {"", "normal"}:
                    return "ERROR: 'priority' is not used with 'tasks'; put priority inside each task object"
                result = self.task_board.create_tasks(tasks=tasks, owner=current_agent_id)
                return self._attach_task_board_bootstrap_if_needed(result)

            result = self.task_board.create_task(
                title=title,
                details=details,
                priority=priority,
                owner=current_agent_id,
            )
            return self._attach_task_board_bootstrap_if_needed(result)

        @tool("set_task_status", parse_docstring=True, error_on_invalid_docstring=True)
        def set_task_status(task_ref: str, status: str, note: str = "", owner: str = "") -> str:
            """Set task status by id/title and record progress notes.

            功能:
                更新任务状态并写入阶段性进展说明。
            适用场景:
                完成一轮采证、验证或修复后推进任务状态。
            不适用场景:
                无新增证据时频繁切换状态制造噪音。
            示例:
                set_task_status(task_ref="task_3", status="done", note="- 证据闭环", owner="main")
            返回值语义:
                OK: 状态更新成功并返回任务摘要。
                ERROR: 任务不存在或状态值非法。

            Args:
                task_ref: 任务引用，支持任务 ID 或标题匹配。
                status: 目标状态，例如 todo/in_progress/blocked/done/cancelled。
                note: 进展说明，记录关键证据或阻塞原因。
                owner: 责任人标识，留空则沿用当前值。

            Returns:
                纯文本结果，前缀为 OK: 或 ERROR:。
            """
            return self.task_board.set_task_status(
                task_ref=task_ref,
                status=status,
                note=note,
                owner=owner,
            )

        @tool("edit_task", parse_docstring=True, error_on_invalid_docstring=True)
        def edit_task(
            task_ref: str,
            title: str = "",
            details: str = "",
            priority: str = "",
            owner: str = "",
            note: str = "",
        ) -> str:
            """Edit task metadata by id/title.

            功能:
                调整任务标题、描述、优先级和责任人，保持任务板与当前策略一致。
            适用场景:
                范围变更、依赖变化、优先级重排。
            不适用场景:
                无实质变化时频繁改动任务元信息。
            示例:
                edit_task(task_ref="task_2", priority="high", note="- 切换为阻塞排查优先")
            返回值语义:
                OK: 修改成功并返回最新任务信息。
                ERROR: 任务不存在或字段值非法。

            Args:
                task_ref: 任务引用，支持任务 ID 或标题匹配。
                title: 新标题，空字符串表示不修改。
                details: 新细节说明，空字符串表示不修改。
                priority: 新优先级，空字符串表示不修改。
                owner: 新责任人，空字符串表示不修改。
                note: 变更备注，说明为什么调整。

            Returns:
                纯文本结果，前缀为 OK: 或 ERROR:。
            """
            return self.task_board.edit_task(
                task_ref=task_ref,
                title=title,
                details=details,
                priority=priority,
                owner=owner,
                note=note,
            )

        @tool("get_task_board", parse_docstring=True, error_on_invalid_docstring=True)
        def get_task_board(view: str = "both", filter_status: str = "") -> str:
            """Read task board markdown in plan/status/both view.

            功能:
                输出任务看板快照，辅助下一轮决策与优先级排序。
            适用场景:
                进入新一轮工具调用前确认当前任务状态。
            不适用场景:
                无决策变化时高频重复读取同一视图。
            示例:
                get_task_board(view="both", filter_status="")
            返回值语义:
                OK: 返回任务板 Markdown 内容。
                ERROR: 视图或过滤参数非法。

            Args:
                view: 视图模式，plan/status/both。
                filter_status: 可选状态过滤条件。

            Returns:
                Markdown 纯文本任务板。
            """
            return self.task_board.get_task_board(view=view, filter_status=filter_status)

        @tool("knowledge_write", parse_docstring=True, error_on_invalid_docstring=True)
        def knowledge_write(
            confirmed_facts: str = "",
            hypotheses: str = "",
            open_questions: str = "",
            evidence: str = "",
            next_actions: str = "",
            do_not_repeat: str = "",
            overwrite: bool = False,
        ) -> str:
            """Write working knowledge sections using plain-text bullets.

            功能:
                将新增事实/证据/假设/下一步沉淀到工作记忆，减少重复探索。
            适用场景:
                每轮出现新增证据后需要更新长期工作上下文。
            不适用场景:
                没有新增信息时重复写入相同内容。
            示例:
                knowledge_write(confirmed_facts="- foo 在 +0x20 读字段", evidence="- decompile + inspect_symbol_usage 输出", overwrite=False)
            返回值语义:
                OK: 返回成功写入的 section 列表。
                ERROR: 未提供任何可写入字段。

            Args:
                confirmed_facts: 已确认事实，按行输入。
                hypotheses: 待验证假设，按行输入。
                open_questions: 未解决问题，按行输入。
                evidence: 证据锚点，按行输入。
                next_actions: 下一步动作，按行输入。
                do_not_repeat: 避免重复动作，按行输入。
                overwrite: 是否覆盖对应 section 旧内容。

            Returns:
                纯文本结果，前缀为 OK: 或 ERROR:。
            """
            mapping = {
                "confirmed_facts": self._clean_lines(confirmed_facts),
                "hypotheses": self._clean_lines(hypotheses),
                "open_questions": self._clean_lines(open_questions),
                "evidence": self._clean_lines(evidence),
                "next_actions": self._clean_lines(next_actions),
                "do_not_repeat": self._clean_lines(do_not_repeat),
            }
            touched: List[str] = []
            for section, values in mapping.items():
                if not values:
                    continue
                self.knowledge_mgr.update(section=section, values=values, overwrite=bool(overwrite), source="tool")
                touched.append(section)
            if not touched:
                return "ERROR: no knowledge fields provided"
            return "OK: updated knowledge sections\n" + "\n".join([f"- {name}" for name in touched])

        @tool("knowledge_read", parse_docstring=True, error_on_invalid_docstring=True)
        def knowledge_read() -> str:
            """Read current working knowledge snapshot.

            功能:
                输出当前工作记忆全文，供计划与决策引用。
            适用场景:
                进入新阶段前需要确认已有事实与待办。
            不适用场景:
                同轮内无变化时重复读取。
            示例:
                knowledge_read()
            返回值语义:
                OK: 返回工作记忆 Markdown。

            Returns:
                Markdown 纯文本工作记忆。
            """
            return self.knowledge_mgr.to_markdown(max_items=40)

        @tool("prune_context_messages", parse_docstring=True, error_on_invalid_docstring=True)
        def prune_context_messages(remove_message_ids: str = "", fold_message_ids: str = "", reason: str = "") -> str:
            """Fold policy messages by Message_xxx IDs.

            功能:
                根据消息 ID 折叠历史内容，降低上下文负载。
            适用场景:
                已识别低价值旧消息，需要压缩上下文。
            不适用场景:
                尝试折叠 system prompt 或首条 user prompt（受保护）。
            示例:
                prune_context_messages(remove_message_ids="Message_001", fold_message_ids="Message_010", reason="压缩上下文")
            返回值语义:
                OK: 返回折叠数量与匹配情况。

            Args:
                remove_message_ids: 待删除消息 ID（兼容字段，按折叠处理），支持多行文本。
                fold_message_ids: 待折叠消息 ID，支持多行文本。
                reason: 本次裁剪理由，便于审计。

            Returns:
                Markdown 纯文本执行报告。
            """
            remove_ids = PolicyManager._extract_message_ids(remove_message_ids)
            fold_ids = PolicyManager._extract_message_ids(fold_message_ids)
            targets: List[str] = []
            seen = set()
            for mid in remove_ids + fold_ids:
                if mid in seen:
                    continue
                seen.add(mid)
                targets.append(mid)

            folded: List[str] = []
            protected: List[str] = []
            already_folded: List[str] = []
            unmatched: List[str] = []
            for mid in targets:
                status = self.policy_mgr.fold_message(mid, reason=reason)
                if status == "folded":
                    folded.append(mid)
                elif status == "protected":
                    protected.append(mid)
                elif status == "already_folded":
                    already_folded.append(mid)
                else:
                    unmatched.append(mid)

            self._precompression_notice_pending = False
            self.obs.emit(
                "context_pruned",
                {
                    "agent_id": current_agent_id,
                    "parent_agent_id": parent_agent_id,
                    "removed_count": 0,
                    "folded_count": len(folded),
                    "removed_message_ids": [],
                    "folded_message_ids": folded,
                    "protected_message_ids": protected,
                    "already_folded_message_ids": already_folded,
                    "unmatched_message_ids": unmatched,
                    "reason": str(reason or "").strip(),
                    "policy_id": self.policy_id,
                    "git_commit": self.git_commit,
                    "loop_mode": self.loop_mode,
                },
            )
            return (
                "OK: context pruning applied\n"
                "- removed_count: 0\n"
                f"- folded_count: {len(folded)}\n"
                f"- protected_count: {len(protected)}\n"
                f"- already_folded_count: {len(already_folded)}\n"
                f"- unmatched_count: {len(unmatched)}\n"
                f"- reason: {str(reason or '').strip() or '(none)'}\n"
                "- note: remove_message_ids is treated as fold targets for compatibility\n"
                + "\n- folded_ids:\n"
                + ("\n".join([f"  - {mid}" for mid in folded]) if folded else "  - (none)")
                + "\n- protected_ids:\n"
                + ("\n".join([f"  - {mid}" for mid in protected]) if protected else "  - (none)")
                + "\n- already_folded_ids:\n"
                + ("\n".join([f"  - {mid}" for mid in already_folded]) if already_folded else "  - (none)")
                + "\n- unmatched_ids:\n"
                + ("\n".join([f"  - {mid}" for mid in unmatched]) if unmatched else "  - (none)")
            )

        @tool("compress_context_8block", parse_docstring=True, error_on_invalid_docstring=True)
        def compress_context_8block(reason: str = "") -> str:
            """Request 8-block context distillation for policy history.

            功能:
                标记下一轮触发上下文蒸馏，压缩历史消息并保留关键记忆。
            适用场景:
                对话消息/字符接近窗口软阈值或硬阈值。
            不适用场景:
                上下文负载低且当前轮无需压缩时。
            示例:
                compress_context_8block(reason="history_soft_threshold")
            返回值语义:
                OK: 成功登记压缩请求。

            Args:
                reason: 压缩触发原因说明。

            Returns:
                纯文本确认信息。
            """
            self._context_compress_requested = True
            self._context_compress_reason = str(reason or "").strip()
            self._precompression_notice_pending = False
            return "OK: 8-block compression requested"

        @tool("manage_context", parse_docstring=True, error_on_invalid_docstring=True)
        def manage_context(action: str, reason: str = "", keep_recent: str = "60") -> str:
            """Backward-compatible context manager for prune/compress/summarize.

            功能:
                将常见上下文管理动作封装为统一入口，兼容旧策略。
            适用场景:
                需要快速执行“保留 N 条并裁剪”或触发压缩。
            不适用场景:
                需要精确 ID 级裁剪时，建议直接用 prune_context_messages。
            示例:
                manage_context(action="prune", reason="keep tail", keep_recent="60")
            返回值语义:
                OK: 返回裁剪或压缩结果。
                ERROR: action 非法。

            Args:
                action: 动作类型，prune/compress/summarize。
                reason: 动作原因说明。
                keep_recent: prune 时保留最近消息条数。

            Returns:
                纯文本结果，前缀为 OK: 或 ERROR:。
            """
            mode = str(action or "").strip().lower()
            if mode == "prune":
                try:
                    keep_n = max(1, int(str(keep_recent or "60").strip()))
                except Exception:
                    keep_n = 60
                active = [row for row in self.policy_mgr.active_refs() if (not row.protected)]
                drop = active[:-keep_n] if len(active) > keep_n else []
                drop_ids = [row.message_id for row in drop]
                if not drop_ids:
                    return "OK: no policy messages folded"
                return prune_context_messages(
                    remove_message_ids="\n".join(drop_ids),
                    fold_message_ids="",
                    reason=reason or f"manage_context prune keep_recent={keep_n}",
                )
            if mode in {"compress", "summarize"}:
                return compress_context_8block(reason=reason or f"manage_context {mode}")
            return "ERROR: action must be one of prune/compress/summarize"

        @tool("spawn_subagent", parse_docstring=True, error_on_invalid_docstring=True)
        def spawn_subagent(task: str, profile: str = "general", context: str = "", priority: str = "normal") -> str:
            """Spawn one asynchronous subagent for independent analysis.

            功能:
                派生子 Agent 处理可并行的专项子任务，不阻塞主线推理。
            适用场景:
                子问题与当前轮结果无强依赖，可独立采证或检索。
            不适用场景:
                子任务必须依赖同轮工具输出才能继续。
            示例:
                spawn_subagent(task="检索 IDAPython 模板", profile="document_agent", context="- 目标: create_structure", priority="high")
            返回值语义:
                OK: 返回 subagent_id；完成结果会自动回流到父 Agent 循环上下文。
                ERROR: 深度超限、task 为空或运行时异常。

            Args:
                task: 子任务说明（Markdown 纯文本）。
                profile: 子 Agent 配置名称，默认 general。
                context: 额外上下文，建议包含证据与约束。
                priority: 优先级标签，默认 normal。

            Returns:
                纯文本结果，前缀为 OK: 或 ERROR:。
            """
            if int(self.agent_depth) >= int(self.max_subagent_depth):
                return (
                    "ERROR: subagent depth limit reached\n"
                    f"- current_depth: {int(self.agent_depth)}\n"
                    f"- max_subagent_depth: {int(self.max_subagent_depth)}"
                )
            task_md = str(task or "").strip()
            if not task_md:
                return "ERROR: missing task"
            sub_id = f"{current_agent_id}.s{len(self.subagent_mgr._subagents) + 1}_{uuid.uuid4().hex[:6]}"
            state = SubAgentState(
                agent_id=sub_id,
                parent_agent_id=current_agent_id,
                profile=str(profile or "general").strip() or "general",
                priority=str(priority or "normal").strip() or "normal",
                task_md=task_md,
                context_md=str(context or "").strip(),
                created_at=time.time(),
                updated_at=time.time(),
            )
            self.subagent_mgr._subagents[sub_id] = state

            async def _runner() -> None:
                try:
                    if self._subagent_sem is None:
                        self._subagent_sem = asyncio.Semaphore(self.subagent_max_parallel)
                    async with self._subagent_sem:
                        result = await self._run_single_subagent(
                            subagent_id=sub_id,
                            parent_agent_id=current_agent_id,
                            request={
                                "profile": state.profile,
                                "priority": state.priority,
                                "task": state.task_md,
                                "context": state.context_md,
                            },
                            user_request=user_request,
                            max_iterations=max(1, int(self.subagent_default_max_iterations)),
                        )
                    state.status = "completed"
                    state.result_md = str(result.get("output", "") or "")
                except Exception as e:
                    state.status = "failed"
                    state.error_text = str(e)
                    state.result_md = f"ERROR: subagent failed: {e}"
                finally:
                    state.updated_at = time.time()
                    self.obs.emit(
                        "subagent_result_received",
                        {
                            "agent_id": state.agent_id,
                            "parent_agent_id": state.parent_agent_id,
                            "status": state.status,
                            "error": state.error_text,
                            "result_preview": AgentUtils.truncate(state.result_md, 1200),
                            "policy_id": self.policy_id,
                            "git_commit": self.git_commit,
                            "loop_mode": self.loop_mode,
                        },
                    )

            asyncio.create_task(_runner())
            self.obs.emit(
                "subagent_spawned",
                {
                    "agent_id": sub_id,
                    "parent_agent_id": current_agent_id,
                    "profile": state.profile,
                    "priority": state.priority,
                    "task_md": AgentUtils.truncate(state.task_md, 2400),
                    "context_md": AgentUtils.truncate(state.context_md, 1800),
                    "policy_id": self.policy_id,
                    "git_commit": self.git_commit,
                    "loop_mode": self.loop_mode,
                },
            )
            return (
                f"OK: spawned subagent_id={sub_id}\n"
                "- mode: async\n"
                "- result_delivery: auto_injected_to_parent_loop"
            )

        @tool("submit_output", parse_docstring=True, error_on_invalid_docstring=True)
        def submit_output(summary: str, key_findings: str = "", artifacts: str = "", next_steps: str = "") -> str:
            """Submit final output and end the main policy loop.

            功能:
                以结构化字段提交最终结论并触发主循环收敛。
            适用场景:
                关键任务关闭且证据闭环完成。
            不适用场景:
                仍存在高优先未完成项或关键证据缺失。
            示例:
                submit_output(summary="完成结构体恢复", key_findings="- 偏移 +0x20 为 size", artifacts="- report.md", next_steps="- 验证调用链")
            返回值语义:
                OK: 终止主循环并缓存最终文本。

            Args:
                summary: 总结结论，必填。
                key_findings: 关键发现列表。
                artifacts: 产物路径或引用列表。
                next_steps: 后续建议动作。

            Returns:
                纯文本确认信息。
            """
            pending = self.subagent_mgr.pending_for_parent(parent_agent_id=current_agent_id)
            if pending:
                lines = [
                    "ERROR: submit_output blocked by running subagents",
                    f"- pending_count: {len(pending)}",
                    "- pending_subagents:",
                ]
                for state in pending:
                    lines.append(
                        f"  - {state.agent_id} (profile={state.profile}, priority={state.priority}, status={state.status})"
                    )
                lines.append("- action: wait for auto-injected subagent results before submit_output")
                return "\n".join(lines)
            self._finalized = True
            payload = {
                "summary": str(summary or "").strip(),
                "key_findings": str(key_findings or "").strip(),
                "artifacts": str(artifacts or "").strip(),
                "next_steps": str(next_steps or "").strip(),
            }
            self._finalize_payload = payload
            self._final_text = self._build_final_text(payload)
            return "OK: submit_output accepted"

        @tool("submit_subagent_output", parse_docstring=True, error_on_invalid_docstring=True)
        def submit_subagent_output(summary: str, findings: str = "") -> str:
            """Submit final output and end one subagent loop.

            功能:
                子 Agent 以精简字段提交结果并结束自身循环。
            适用场景:
                子任务达到可交付状态，需要回传主 Agent。
            不适用场景:
                仍缺核心证据时提前结束。
            示例:
                submit_subagent_output(summary="完成文档检索", findings="- 命中 templates/foo.py")
            返回值语义:
                OK: 终止子循环并缓存子结果文本。

            Args:
                summary: 子任务结论摘要，必填。
                findings: 关键发现列表。

            Returns:
                纯文本确认信息。
            """
            self._finalized = True
            payload = {
                "summary": str(summary or "").strip(),
                "findings": str(findings or "").strip(),
            }
            self._finalize_payload = payload
            self._final_text = self._build_subagent_final_text(payload)
            return "OK: submit_subagent_output accepted"

        base_tools: List[BaseTool] = [
            create_task,
            set_task_status,
            edit_task,
            get_task_board,
            knowledge_write,
            knowledge_read,
            spawn_subagent,
        ]
        if bool(include_context_tools):
            base_tools.extend(
                [
                    prune_context_messages,
                    compress_context_8block,
                    manage_context,
                ]
            )
        if str(finalize_mode or "main").strip().lower() == "subagent":
            base_tools.append(submit_subagent_output)
        else:
            base_tools.append(submit_output)
        return base_tools

    def _build_idapython_executor_tools(
        self,
        *,
        state: Dict[str, Any],
        kb_root: Optional[Path],
        max_iterations: int,
    ) -> List[BaseTool]:
        @tool("submit_idapython_script", parse_docstring=True, error_on_invalid_docstring=True)
        async def submit_idapython_script(script: str, fix_note: str = "") -> str:
            """Submit one repaired script candidate and execute it.

            功能:
                提交一版脚本修复候选，立即执行并返回结果；成功后结束当前 IDAPythonAgent 循环。
            适用场景:
                已根据报错完成最小修改，需要验证脚本是否可执行。
            不适用场景:
                还未形成可执行脚本版本。
            示例:
                submit_idapython_script(script="import idc\\n__result__ = idc.get_func_name(ea)", fix_note="补充缺失 import")
            返回值语义:
                OK: 脚本执行成功并返回执行输出。
                ERROR: 脚本执行失败或超过最大重试次数。

            Args:
                script: 修复后的完整脚本文本。
                fix_note: 本次修复说明（可选）。

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

            state["attempts"] = int(state.get("attempts", 0)) + 1
            state["last_script"] = candidate
            state["last_fix_note"] = str(fix_note or "").strip()

            result = await asyncio.to_thread(
                self.ida_client.execute_script,
                candidate,
                state.get("context", {}),
            )
            rendered, is_ok = self._render_idapython_execution_output(result)
            state["last_output"] = rendered
            if is_ok:
                state["succeeded"] = True
                state["last_success_output"] = rendered
                self._finalized = True
                self._final_text = rendered
                return rendered
            state["last_error_output"] = rendered
            return rendered

        @tool("search", parse_docstring=True, error_on_invalid_docstring=True)
        def search(pattern: str) -> str:
            """Search the IDAPython knowledge base by regex and return path:line hits.

            功能:
                用正则在知识库中检索 API 用法、样例脚本和文档锚点。
            适用场景:
                当前错误需要查询 API 正确用法或替代方案。
            不适用场景:
                仅需重试已知最小修复时。
            示例:
                search(pattern="ida_typeinf\\.parse_decls")
            返回值语义:
                OK: 返回匹配的文件路径与行号。
                ERROR: 知识库不可用、正则非法或检索失败。

            Args:
                pattern: Python 正则表达式。

            Returns:
                Markdown 纯文本命中列表。
            """
            if kb_root is None:
                return "ERROR: search is disabled because idapython kb directory is unavailable"
            hits = search_regex(root_dir=kb_root, pattern=str(pattern or ""), max_hits=120)
            lines = [
                "# Search Hits",
                f"- pattern: {str(pattern or '').strip() or '(empty)'}",
                f"- hit_count: {len(hits)}",
            ]
            if not hits:
                lines.append("No matches found.")
                return "\n".join(lines)
            lines.append("")
            for row in hits:
                lines.append(f"- {row.path}:{row.line}")
            return "\n".join(lines)

        @tool("read_file", parse_docstring=True, error_on_invalid_docstring=True)
        def read_file(path: str, line: int) -> str:
            """Read file content around one line from IDAPython knowledge base.

            功能:
                根据搜索命中读取文件上下文，返回带行号的文本片段。
            适用场景:
                search 命中后需要查看具体 API 调用示例与参数形态。
            不适用场景:
                无有效路径或行号时。
            示例:
                read_file(path="docs/ida_typeinf.rst", line=4300)
            返回值语义:
                OK: 返回 `[line] content` 形式的片段。
                ERROR: 知识库不可用、路径越界或读取失败。

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

        tools: List[BaseTool] = [submit_idapython_script]
        if kb_root is not None:
            tools = [search, read_file, submit_idapython_script]
        return tools

    async def _run_execute_idapython_agent(
        self,
        *,
        script: str,
        context: Optional[Dict[str, Any]],
        max_iterations: int = 8,
    ) -> str:
        script_text = str(script or "").strip()
        if not script_text:
            return "ERROR: missing script"

        destructive_ops = AgentUtils.find_destructive_struct_ops(script_text)
        if destructive_ops:
            return (
                "ERROR: destructive struct operation is blocked in execute_idapython.\n"
                f"Blocked operations: {destructive_ops}\n"
                "Reason: deleting existing structs can destroy recovered evidence and invalidate before/after acceptance.\n"
                "Use non-destructive updates instead."
            )

        runtime_context = context if isinstance(context, dict) else {}
        max_iters = max(1, int(max_iterations))

        first_result = await asyncio.to_thread(self.ida_client.execute_script, script_text, runtime_context)
        first_output, first_ok = self._render_idapython_execution_output(first_result)
        if first_ok or max_iters == 1:
            return first_output
        if int(self.agent_depth) + 1 > int(self.max_subagent_depth):
            return (
                f"{first_output}\n"
                "ERROR: idapython repair agent depth limit reached\n"
                f"- current_depth: {int(self.agent_depth)}\n"
                f"- max_subagent_depth: {int(self.max_subagent_depth)}"
            )

        kb_root = self._resolve_idapython_kb_root()
        state: Dict[str, Any] = {
            "attempts": 1,
            "context": runtime_context,
            "last_script": script_text,
            "last_output": first_output,
            "last_error_output": first_output,
            "last_success_output": "",
            "succeeded": False,
        }

        task_lines = [
            "- [x] 执行初始 IDAPython 脚本并收集错误",
            "- [ ] 针对报错做最小修改并提交新脚本",
            "- [ ] 若报错不明确，调用 search/read_file 检索知识库",
            "- [ ] 脚本成功后结束循环",
        ]
        if kb_root is None:
            task_lines[2] = "- [x] 知识库不可用，改用已有上下文做最小修复"
        context_md = "\n".join(
            [
                "## Initial Script",
                "```python",
                AgentUtils.truncate(script_text, 3600),
                "```",
                "",
                "## Initial Failure",
                AgentUtils.truncate(first_output, 3200),
                "",
                "## Runtime Context Keys",
                ", ".join(sorted([str(k) for k in runtime_context.keys()])) or "(empty)",
                "",
                "## Knowledge Base",
                str(kb_root) if kb_root is not None else "(disabled)",
            ]
        )

        child = self._spawn_isolated_subagent_core()
        child.tool_profile = "idapython_executor"
        child.expert_tools = child._build_idapython_executor_tools(
            state=state,
            kb_root=kb_root,
            max_iterations=max_iters,
        )
        child.expert_tool_map = {row.name: row for row in child.expert_tools}

        subagent_id = f"execpy_{uuid.uuid4().hex[:8]}"
        await child._run_subagent_policy_loop(
            user_request="修复并执行 IDAPython 脚本，直到成功或达到最大迭代。",
            request={
                "profile": "idapython_executor" if kb_root is not None else "idapython_executor_no_kb",
                "priority": "high",
                "task": "\n".join(task_lines),
                "context": context_md,
            },
            max_iterations=max(1, max_iters - 1),
            agent_id=subagent_id,
            parent_agent_id="main",
        )

        if state.get("succeeded") and str(state.get("last_success_output", "")).strip():
            return str(state.get("last_success_output", "")).strip()

        last_error = str(state.get("last_error_output", "") or state.get("last_output", "")).strip()
        if not last_error:
            return f"ERROR: execute_idapython failed and no error output captured (max_iterations={max_iters})"
        if "max_iterations_reached" not in last_error:
            last_error += f"\nERROR: max_iterations_reached ({max_iters})"
        return last_error

    async def _run_single_subagent(
        self,
        *,
        subagent_id: str,
        parent_agent_id: str,
        request: Dict[str, Any],
        user_request: str,
        max_iterations: int,
    ) -> Dict[str, Any]:
        profile_name = str(request.get("profile", "general") or "general").strip() or "general"
        if int(self.agent_depth) + 1 > int(self.max_subagent_depth):
            return {
                "subagent_id": subagent_id,
                "profile": profile_name,
                "priority": str(request.get("priority", "normal") or "normal"),
                "task": str(request.get("task", "") or ""),
                "output": (
                    "ERROR: subagent depth limit reached\n"
                    f"- current_depth: {int(self.agent_depth)}\n"
                    f"- max_subagent_depth: {int(self.max_subagent_depth)}"
                ),
            }

        child = self._spawn_isolated_subagent_core()
        started = time.perf_counter()
        result = await child._run_subagent_policy_loop(
            user_request=str(user_request or "").strip(),
            request=request,
            max_iterations=max(1, int(max_iterations)),
            agent_id=subagent_id,
            parent_agent_id=parent_agent_id,
        )
        latency = time.perf_counter() - started
        output = str(result.get("final_text", "") or "").strip() or "(empty subagent output)"

        self.obs.emit(
            "subagent_plan_generated",
            {
                "agent_id": subagent_id,
                "parent_agent_id": parent_agent_id,
                "profile": profile_name,
                "priority": str(request.get("priority", "normal") or "normal"),
                "task_md": AgentUtils.truncate(str(request.get("task", "") or ""), 2400),
                "output_preview": AgentUtils.truncate(output, 1600),
                "latency_s": round(latency, 4),
                "policy_id": self.policy_id,
                "git_commit": self.git_commit,
                "loop_mode": self.loop_mode,
            },
        )
        return {
            "subagent_id": subagent_id,
            "profile": profile_name,
            "priority": str(request.get("priority", "normal") or "normal"),
            "task": str(request.get("task", "") or ""),
            "output": output,
        }

    def _spawn_isolated_subagent_core(self) -> "ReverseExpertAgentCore":
        child = ReverseExpertAgentCore(
            ida_service_url=self.ida_client.base_url,
            openai_api_key=self._openai_api_key,
            openai_base_url=self._openai_base_url,
            model=self.model,
            prompt_root=str(self.prompt_manager.prompt_root),
            agent_depth=int(self.agent_depth) + 1,
            idapython_kb_dir=self.idapython_kb_dir,
        )
        # Share parent's session logger so subagent logs go to same database
        child.enable_session_log = self.enable_session_log
        child.session_logger = self.session_logger
        child.last_session_id = self.last_session_id
        child.session_db_path = self.session_db_path
        child.obs = self.obs
        child.tool_result_log_chars = self.tool_result_log_chars
        child.message_log_chars = self.message_log_chars
        child.llm_max_retries = self.llm_max_retries
        child.subagent_max_retries = self.subagent_max_retries
        child.tool_call_max_parallel = self.tool_call_max_parallel
        child.tool_call_collect_all_errors = self.tool_call_collect_all_errors
        child.tool_profile = self.tool_profile
        child.expert_tools = self._build_expert_tools(self.tool_profile)
        child.expert_tool_map = {row.name: row for row in child.expert_tools}
        child.context_window_messages = self.context_window_messages
        child.subagent_max_context_chars = self.subagent_max_context_chars
        child.subagent_max_parallel = self.subagent_max_parallel
        child.policy_history_max_messages = self.policy_history_max_messages
        child.policy_history_max_chars = self.policy_history_max_chars
        child.policy_history_soft_ratio = self.policy_history_soft_ratio
        child.policy_history_keep_tail_messages = self.policy_history_keep_tail_messages
        child.policy_history_distill_max_messages = self.policy_history_distill_max_messages
        child.context_fold_placeholder = self.context_fold_placeholder
        child.max_subagent_depth = self.max_subagent_depth
        child.subagent_default_max_iterations = self.subagent_default_max_iterations
        child.idapython_kb_dir = self.idapython_kb_dir
        return child

    async def _run_subagent_policy_loop(
        self,
        *,
        user_request: str,
        request: Dict[str, Any],
        max_iterations: int,
        agent_id: str,
        parent_agent_id: str,
    ) -> Dict[str, Any]:
        profile_name = str(request.get("profile", "general") or "general").strip() or "general"
        template_name = f"subagents/{profile_name}.md"
        try:
            system_prompt = self.prompt_manager.render(template_name, {"profile_name": profile_name})
        except FileNotFoundError:
            system_prompt = self.prompt_manager.render("subagents/general.md", {"profile_name": profile_name})

        human_prompt = self.prompt_manager.render(
            "agent/subagent_user.md",
            {
                "user_request": str(user_request or ""),
                "task": str(request.get("task", "") or "").strip(),
                "context": AgentUtils.truncate(str(request.get("context", "") or "").strip(), self.subagent_max_context_chars),
            },
        )

        max_iters = max(1, int(max_iterations))
        messages: List[Any] = []
        init_turn_id = f"{agent_id}:0:subpolicy:init"
        self.policy_mgr.append_message(
            messages=messages,
            message_obj=SystemMessage(content=system_prompt),
            role="system",
            turn_id=init_turn_id,
            protected=True,
        )
        self.policy_mgr.append_message(
            messages=messages,
            message_obj=HumanMessage(content=human_prompt),
            role="user",
            turn_id=init_turn_id,
            protected=True,
        )
        for iteration in range(1, max_iters + 1):
            turn_id = f"{agent_id}:{iteration}:subpolicy:{uuid.uuid4().hex[:8]}"
            subagent_updates = self.subagent_mgr.drain_completed_updates(parent_agent_id=agent_id)
            if subagent_updates:
                self.policy_mgr.append_message(
                    messages=messages,
                    message_obj=HumanMessage(content=subagent_updates),
                    role="user",
                    turn_id=turn_id,
                    protected=False,
                )
            compression_prompt = await self._maybe_compress_policy_history(
                messages=messages,
                iteration=iteration,
                user_request=user_request,
                agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                turn_id=turn_id,
            )
            if str(compression_prompt or "").strip():
                self.policy_mgr.append_message(
                    messages=messages,
                    message_obj=HumanMessage(content=str(compression_prompt).strip()),
                    role="user",
                    turn_id=turn_id,
                    protected=False,
                )

            include_context_tools = self._should_expose_context_tools(messages=messages, compression_prompt=compression_prompt)
            runtime_tools = self._make_runtime_tools(
                current_agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                user_request=user_request,
                max_iterations=max_iters,
                include_context_tools=include_context_tools,
                finalize_mode="subagent",
            )
            all_tools = list(self.expert_tools) + list(runtime_tools)
            tool_map: Dict[str, BaseTool] = {tool_obj.name: tool_obj for tool_obj in all_tools}
            llm_with_tools = self.llm.bind_tools(all_tools)
            # Log bound tools for observability
            self.obs.emit(
                "tools_bound",
                {
                    "turn_id": turn_id,
                    "agent_id": agent_id,
                    "tools": [{"name": t.name, "description": getattr(t, "description", "")} for t in all_tools],
                },
            )

            interaction_started = time.perf_counter()
            response = await self._ainvoke_with_retry(llm_with_tools, messages, max_retries=self.llm_max_retries)
            response_content = getattr(response, "content", "") or ""
            response_text = AgentUtils.content_to_text(response_content)
            tool_calls = self._normalize_tool_calls(getattr(response, "tool_calls", None), turn_id=turn_id)
            self.policy_mgr.append_message(
                messages=messages,
                message_obj=AIMessage(content=response_text, tool_calls=tool_calls),
                role="assistant",
                turn_id=turn_id,
                protected=False,
            )

            assistant_text = response_text.strip()
            if assistant_text:
                self.context_mgr.append(
                    role="assistant",
                    source="llm:subpolicy",
                    content=assistant_text,
                    turn_id=turn_id,
                    agent_id=agent_id,
                )

            call_rows = tool_calls if isinstance(tool_calls, list) else []
            if call_rows:
                outputs = await self._execute_tool_calls(
                    turn_id=turn_id,
                    agent_id=agent_id,
                    parent_agent_id=parent_agent_id,
                    iteration=iteration,
                    tool_calls=call_rows,
                    tool_map=tool_map,
                )
                for idx, row in enumerate(outputs, start=1):
                    tool_call_id = str(row.get("tool_call_id", "") or call_rows[idx - 1].get("id", "") or f"{turn_id}:tool:{idx}")
                    result_text = str(row.get("result", "") or "")
                    self.policy_mgr.append_message(
                        messages=messages,
                        message_obj=ToolMessage(content=result_text, tool_call_id=tool_call_id),
                        role="tool",
                        turn_id=turn_id,
                        protected=False,
                    )
            latency_s = time.perf_counter() - interaction_started
            # 记录完整的消息历史，包括 system prompt 和之前的对话
            self.obs.emit(
                "llm_interaction",
                {
                    "turn_id": turn_id,
                    "agent_id": agent_id,
                    "agent_name": self.__class__.__name__,
                    "iteration": iteration,
                    "phase": "subagent",
                    "messages": self._serialize_messages_for_log(messages),
                    "latency_s": round(latency_s, 4),
                },
            )
            if self._finalized:
                return {
                    "final_text": self._final_text or self._build_subagent_final_text(),
                    "iterations_used": iteration,
                    "completed": True,
                    "reason": "submit_subagent_output_called",
                }

        return {
            "final_text": self._build_subagent_incomplete_text(iteration=max_iters, max_iterations=max_iters),
            "iterations_used": max_iters,
            "completed": False,
            "reason": "max_iterations_reached",
        }

    def _build_main_system_prompt(self) -> str:
        return self.prompt_manager.render("agent/reverse_expert_system.md", {})

    async def _execute_tool_calls(
        self,
        *,
        turn_id: str,
        agent_id: str,
        parent_agent_id: str,
        iteration: int,
        tool_calls: List[Dict[str, Any]],
        tool_map: Dict[str, BaseTool],
    ) -> List[Dict[str, Any]]:
        started = time.perf_counter()
        max_parallel = max(1, int(self.tool_call_max_parallel))
        sem = asyncio.Semaphore(max_parallel)

        async def _run_one(idx: int, call: Dict[str, Any]) -> Dict[str, Any]:
            tool_name = str(call.get("name", "") or "")
            tool_args = call.get("args", {})
            if not isinstance(tool_args, dict):
                tool_args = {"raw": tool_args}
            tool_call_id = str(call.get("id", "") or f"{turn_id}:tool:{idx}")
            one_started = time.perf_counter()

            async with sem:
                try:
                    tool_obj = tool_map.get(tool_name)
                    if not tool_obj:
                        result = f"ERROR: unknown tool '{tool_name}'"
                    else:
                        result_obj = await tool_obj.ainvoke(tool_args)
                        result = str(result_obj)
                except Exception as e:
                    result = f"ERROR: {e}"
            is_error = result.startswith("ERROR:") or result.startswith("Error:") or result.startswith("[ERROR]")
            mutation_effective = None
            if self._is_mutating_tool(tool_name):
                mutation_effective = self._parse_mutation_effective(result)
            return {
                "index": int(idx),
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "is_error": is_error,
                "mutation_effective": mutation_effective,
                "result": result,
                "duration_ms": int((time.perf_counter() - one_started) * 1000),
            }

        jobs = [
            asyncio.create_task(_run_one(idx, call))
            for idx, call in enumerate(tool_calls, start=1)
        ]
        outputs = await asyncio.gather(*jobs)
        outputs = sorted(outputs, key=lambda row: int(row.get("index", 0)))

        for row in outputs:
            result_text = str(row.get("result", "") or "")
            if row.get("mutation_effective") is True:
                self._effective_mutation_count += 1
                if self._is_type_application_tool(str(row.get("tool_name", "") or "")):
                    self._effective_type_application_count += 1
            self.context_mgr.append(
                role="tool",
                source=f"policy:{str(row.get('tool_name', '') or '')}",
                content=result_text,
                turn_id=turn_id,
                agent_id=agent_id,
            )

        self.obs.emit(
            "tool_batch_executed",
            {
                "turn_id": turn_id,
                "agent_id": agent_id,
                "parent_agent_id": parent_agent_id,
                "iteration": int(iteration),
                "batch_size": len(outputs),
                "parallel_limit": max_parallel,
                "success_count": len([row for row in outputs if not bool(row.get("is_error", False))]),
                "error_count": len([row for row in outputs if bool(row.get("is_error", False))]),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "durations_ms": [int(row.get("duration_ms", 0)) for row in outputs],
                "tool_calls": [
                    {
                        "tool_call_id": str(row.get("tool_call_id", "") or ""),
                        "tool_name": str(row.get("tool_name", "") or ""),
                        "is_error": bool(row.get("is_error", False)),
                        "mutation_effective": row.get("mutation_effective"),
                        "duration_ms": int(row.get("duration_ms", 0) or 0),
                        "result_preview": AgentUtils.truncate(str(row.get("result", "") or ""), 600),
                    }
                    for row in outputs
                ],
                "policy_id": self.policy_id,
                "git_commit": self.git_commit,
                "loop_mode": self.loop_mode,
            },
        )
        return outputs

    def _should_expose_context_tools(self, *, messages: List[Any], compression_prompt: str) -> bool:
        if str(compression_prompt or "").strip():
            return True
        if self._context_compress_requested or self._precompression_notice_pending:
            return True
        usage = self.policy_mgr.calculate_usage(messages)
        max_messages = max(10, int(self.policy_history_max_messages))
        max_chars = max(40000, int(self.policy_history_max_chars))
        soft_messages = max(8, int(max_messages * float(self.policy_history_soft_ratio)))
        soft_chars = max(32000, int(max_chars * float(self.policy_history_soft_ratio)))
        return (
            int(usage.get("message_count", 0)) > soft_messages
            or int(usage.get("total_chars", 0)) > soft_chars
        )

    def _build_final_text(self, payload: Optional[Dict[str, str]] = None) -> str:
        data = payload or self._finalize_payload or {}
        lines: List[str] = [
            "# Reverse Expert Final Summary",
            "",
            "## Summary",
            str(data.get("summary", "")).strip() or "(empty)",
            "",
            "## Key Findings",
            str(data.get("key_findings", "")).strip() or "(empty)",
            "",
            "## Artifacts",
            str(data.get("artifacts", "")).strip() or "(empty)",
            "",
            "## Next Steps",
            str(data.get("next_steps", "")).strip() or "(empty)",
            "",
            "## Task Board",
            self.task_board.get_task_board(view="both"),
            "",
            "## Working Knowledge",
            self.knowledge_mgr.to_markdown(max_items=20),
            "",
        ]
        return "\n".join(lines).strip() + "\n"

    def _build_subagent_final_text(self, payload: Optional[Dict[str, str]] = None) -> str:
        data = payload or self._finalize_payload or {}
        lines: List[str] = [
            "# Reverse SubAgent Final",
            "",
            "## Summary",
            str(data.get("summary", "")).strip() or "(empty)",
            "",
            "## Findings",
            str(data.get("findings", "")).strip() or "(empty)",
            "",
        ]
        return "\n".join(lines).strip() + "\n"

    def _build_incomplete_text(self, *, iteration: int, max_iterations: int) -> str:
        lines: List[str] = [
            "# Reverse Expert Incomplete",
            "",
            f"- iterations_used: {int(iteration)}",
            f"- max_iterations: {int(max_iterations)}",
            "- reason: max_iterations_reached_without_submit_output",
            "",
            "## Task Board",
            self.task_board.get_task_board(view="both"),
            "",
            "## Working Knowledge",
            self.knowledge_mgr.to_markdown(max_items=20),
            "",
        ]
        return "\n".join(lines).strip() + "\n"

    def _build_subagent_incomplete_text(self, *, iteration: int, max_iterations: int) -> str:
        lines: List[str] = [
            "# Reverse SubAgent Incomplete",
            "",
            f"- iterations_used: {int(iteration)}",
            f"- max_iterations: {int(max_iterations)}",
            "- reason: max_iterations_reached_without_submit_subagent_output",
            "",
            "## Task Board",
            self.task_board.get_task_board(view="both"),
            "",
            "## Working Knowledge",
            self.knowledge_mgr.to_markdown(max_items=20),
            "",
        ]
        return "\n".join(lines).strip() + "\n"

    async def _run_policy_loop(
        self,
        *,
        user_request: str,
        max_iterations: int,
        agent_id: str,
        parent_agent_id: str,
    ) -> Dict[str, Any]:
        base_runtime_tools = self._make_runtime_tools(
            current_agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            user_request=user_request,
            max_iterations=max_iterations,
            include_context_tools=False,
            finalize_mode="main",
        )
        base_tools = list(self.expert_tools) + list(base_runtime_tools)
        system_prompt = self._build_main_system_prompt()

        max_iters = int(max_iterations)
        if max_iters <= 0:
            max_iters = 10**9

        messages: List[Any] = []
        init_turn_id = f"{agent_id}:0:policy:init"
        self.policy_mgr.append_message(
            messages=messages,
            message_obj=SystemMessage(content=system_prompt),
            role="system",
            turn_id=init_turn_id,
            protected=True,
        )
        self.policy_mgr.append_message(
            messages=messages,
            message_obj=HumanMessage(content=str(user_request or "").strip()),
            role="user",
            turn_id=init_turn_id,
            protected=True,
        )

        for iteration in range(1, max_iters + 1):
            turn_id = f"{agent_id}:{iteration}:policy:{uuid.uuid4().hex[:8]}"
            subagent_updates = self.subagent_mgr.drain_completed_updates(parent_agent_id=agent_id)
            if subagent_updates:
                self.policy_mgr.append_message(
                    messages=messages,
                    message_obj=HumanMessage(content=subagent_updates),
                    role="user",
                    turn_id=turn_id,
                    protected=False,
                )
            compression_prompt = await self._maybe_compress_policy_history(
                messages=messages,
                iteration=iteration,
                user_request=user_request,
                agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                turn_id=turn_id,
            )
            if str(compression_prompt or "").strip():
                self.policy_mgr.append_message(
                    messages=messages,
                    message_obj=HumanMessage(content=str(compression_prompt).strip()),
                    role="user",
                    turn_id=turn_id,
                    protected=False,
                )

            include_context_tools = self._should_expose_context_tools(messages=messages, compression_prompt=compression_prompt)
            runtime_tools = self._make_runtime_tools(
                current_agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                user_request=user_request,
                max_iterations=max_iters,
                include_context_tools=include_context_tools,
                finalize_mode="main",
            )
            all_tools = list(self.expert_tools) + list(runtime_tools)
            tool_map: Dict[str, BaseTool] = {tool_obj.name: tool_obj for tool_obj in all_tools}
            llm_with_tools = self.llm.bind_tools(all_tools)
            # Log bound tools for observability
            self.obs.emit(
                "tools_bound",
                {
                    "turn_id": turn_id,
                    "agent_id": agent_id,
                    "tools": [{"name": t.name, "description": getattr(t, "description", "")} for t in all_tools],
                },
            )

            interaction_started = time.perf_counter()
            try:
                response = await self._ainvoke_with_retry(llm_with_tools, messages, max_retries=self.llm_max_retries)
            except Exception as e:
                self.obs.emit(
                    "llm_response_failed",
                    {
                        "turn_id": turn_id,
                        "error": str(e),
                        "messages": self._serialize_messages_for_log(messages),
                    },
                )
                raise

            response_content = getattr(response, "content", "") or ""
            response_text = AgentUtils.content_to_text(response_content)
            tool_calls = self._normalize_tool_calls(getattr(response, "tool_calls", None), turn_id=turn_id)
            self.policy_mgr.append_message(
                messages=messages,
                message_obj=AIMessage(content=response_text, tool_calls=tool_calls),
                role="assistant",
                turn_id=turn_id,
                protected=False,
            )

            assistant_text = response_text.strip()
            if assistant_text:
                self.context_mgr.append(
                    role="assistant",
                    source="llm:policy",
                    content=assistant_text,
                    turn_id=turn_id,
                    agent_id=agent_id,
                )

            call_rows = tool_calls if isinstance(tool_calls, list) else []
            if call_rows:
                outputs = await self._execute_tool_calls(
                    turn_id=turn_id,
                    agent_id=agent_id,
                    parent_agent_id=parent_agent_id,
                    iteration=iteration,
                    tool_calls=call_rows,
                    tool_map=tool_map,
                )
                for idx, row in enumerate(outputs, start=1):
                    tool_call_id = str(row.get("tool_call_id", "") or call_rows[idx - 1].get("id", "") or f"{turn_id}:tool:{idx}")
                    result_text = str(row.get("result", "") or "")
                    self.policy_mgr.append_message(
                        messages=messages,
                        message_obj=ToolMessage(content=result_text, tool_call_id=tool_call_id),
                        role="tool",
                        turn_id=turn_id,
                        protected=False,
                    )
            latency_s = time.perf_counter() - interaction_started
            # 记录完整的消息历史，包括 system prompt 和之前的对话
            self.obs.emit(
                "llm_interaction",
                {
                    "turn_id": turn_id,
                    "agent_id": agent_id,
                    "agent_name": self.__class__.__name__,
                    "iteration": iteration,
                    "phase": "main" if agent_id == "main" else "subagent",
                    "messages": self._serialize_messages_for_log(messages),
                    "latency_s": round(latency_s, 4),
                },
            )

            if self._finalized:
                return {
                    "final_text": self._final_text or self._build_final_text(),
                    "iterations_used": iteration,
                    "completed": True,
                    "reason": "submit_output_called",
                }

        return {
            "final_text": self._build_incomplete_text(iteration=max_iters, max_iterations=max_iterations),
            "iterations_used": max_iters,
            "completed": False,
            "reason": "max_iterations_reached",
        }

    async def run(self, user_request: str, max_iterations: int = 30) -> str:
        self._init_session_logger()
        self._reset_runtime_state()

        run_started = time.perf_counter()
        self.obs.emit(
            "session_start",
            {
                "user_request": str(user_request or "").strip(),
                "model": self.model,
                "ida_service_url": self.ida_client.base_url,
                "tool_profile": self.tool_profile,
                "max_iterations": int(max_iterations),
                "session_id": self.last_session_id,
                "plan_mode": False,
                "policy_id": self.policy_id,
                "git_commit": self.git_commit,
                "loop_mode": self.loop_mode,
            },
        )

        try:
            result = await self._run_policy_loop(
                user_request=str(user_request or "").strip(),
                max_iterations=int(max_iterations),
                agent_id="main",
                parent_agent_id="",
            )
            final_text = str(result.get("final_text", "") or "")
            iterations_used = int(result.get("iterations_used", 0) or 0)
            completed = bool(result.get("completed", False))
            event_name = "session_complete" if completed else "session_incomplete"
            self.obs.emit(
                event_name,
                {
                    "duration_s": round(time.perf_counter() - run_started, 4),
                    "iterations_used": iterations_used,
                    "final_response": AgentUtils.truncate(final_text, 12000),
                    "effective_mutation_count": int(self._effective_mutation_count),
                    "effective_type_application_count": int(self._effective_type_application_count),
                    "completed_reason": str(result.get("reason", "")),
                    "policy_id": self.policy_id,
                    "git_commit": self.git_commit,
                    "loop_mode": self.loop_mode,
                },
            )
            return final_text
        except Exception as e:
            self.obs.emit(
                "session_incomplete",
                {
                    "duration_s": round(time.perf_counter() - run_started, 4),
                    "error": str(e),
                    "policy_id": self.policy_id,
                    "git_commit": self.git_commit,
                    "loop_mode": self.loop_mode,
                },
            )
            raise

    def get_last_session_id(self) -> Optional[str]:
        return self.last_session_id

    def get_session_db_path(self) -> Optional[str]:
        return self.session_db_path

    def log_runtime_event(self, event: str, payload: Dict[str, Any]) -> None:
        self.obs.emit(event, payload)


class ReverseExpertAgentCoreSync:
    """Synchronous wrapper for scripts."""

    def __init__(self, *args, **kwargs):
        self.agent = ReverseExpertAgentCore(*args, **kwargs)

    def run(self, user_request: str, max_iterations: int = 30) -> str:
        return asyncio.run(self.agent.run(user_request=user_request, max_iterations=max_iterations))

    @property
    def ida_client(self) -> IDAClient:
        return self.agent.ida_client

    def get_last_session_id(self) -> Optional[str]:
        return self.agent.get_last_session_id()

    def get_session_db_path(self) -> Optional[str]:
        return self.agent.get_session_db_path()

    def log_runtime_event(self, event: str, payload: Dict[str, Any]) -> None:
        self.agent.log_runtime_event(event, payload)
