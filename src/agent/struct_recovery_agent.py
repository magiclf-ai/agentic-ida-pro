"""Struct recovery module: extension + runtime core + facade."""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI

from .context_distiller import ContextDistillerAgent
from .context_manager import ContextManager
from .ida_client import IDAClient
from .idapython_agent import IDAPythonTaskAgent
from .knowledge_manager import KnowledgeManager
from .models import ContextMessageRow, PolicyMessageRef
from .observability import ObservabilityHub
from .policy_manager import PolicyManager
from .prompt_manager import PromptManager
from .session_logger import AgentSessionLogger
from .subagent_runtime import SubAgentRuntime
from .subagent_manager import SubAgentManager
from .task_board import TaskBoard
from .tools import full_tools as all_registered_tools, set_ida_client, tools as registered_tools
from .tool_registry import ExpertToolRegistry
from .utils import AgentUtils


class StructRecoveryToolExecutionExtension:
    """Adds mutation_effective annotations and counters for struct recovery sessions."""

    MUTATING_TOOL_NAMES = {
        "create_structure",
        "set_identifier_type",
        "set_function_comment",
    }
    TYPE_APPLICATION_TOOL_NAMES = {
        "set_identifier_type",
    }

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._effective_mutation_count = 0
        self._effective_type_application_count = 0

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

    def annotate_tool_result(self, *, tool_name: str, result: str, is_error: bool) -> Dict[str, Any]:
        del is_error
        if str(tool_name or "") not in self.MUTATING_TOOL_NAMES:
            return {}
        return {
            "mutation_effective": self._parse_mutation_effective(result),
        }

    def on_tool_result(self, row: Dict[str, Any]) -> None:
        if row.get("mutation_effective") is not True:
            return
        self._effective_mutation_count += 1
        if str(row.get("tool_name", "") or "") in self.TYPE_APPLICATION_TOOL_NAMES:
            self._effective_type_application_count += 1

    def build_session_complete_payload(self) -> Dict[str, Any]:
        return {
            "effective_mutation_count": int(self._effective_mutation_count),
            "effective_type_application_count": int(self._effective_type_application_count),
        }


class StructRecoveryRuntimeCore:
    """LLM-driven struct-recovery runtime with a single tool-call policy loop."""

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
        tool_profile: str = "execute_only",
        runtime_name: str = "",
        tool_execution_extension_factory: Optional[Callable[[], Any]] = None,
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
        self.tool_profile = str(tool_profile or "execute_only").strip() or "execute_only"
        self.runtime_name = str(runtime_name or "").strip() or self.__class__.__name__
        self.tool_execution_extension_factory = tool_execution_extension_factory
        self.tool_execution_extension = self._new_tool_execution_extension()
        self.max_subagent_depth = 4
        self.subagent_default_max_iterations = 8
        self.expert_tools: List[Any] = []
        self.expert_tool_map: Dict[str, Any] = {}

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
        self.enable_llm_console_log = self._llm_console_logging_enabled()
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

        self.subagent_runtime = SubAgentRuntime(self)
        self.idapython_agent = IDAPythonTaskAgent(self)
        self.expert_tools = self._build_expert_tools(self.tool_profile)
        self.expert_tool_map = {row.name: row for row in self.expert_tools}


    def _debug_enabled(self) -> bool:
        value = str(os.getenv("AGENT_DEBUG", os.getenv("AGENT_DEBUG_TRACE", "0"))).strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _llm_console_logging_enabled(self) -> bool:
        value = str(os.getenv("AGENT_LLM_LOG_STDOUT", "1")).strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _print_llm_request(self, *, turn_id: str, agent_id: str, iteration: int, messages_count: int) -> None:
        if not self.enable_llm_console_log:
            return
        print(
            f"[LLM] request turn={turn_id} agent={agent_id} iter={int(iteration)} "
            f"messages={int(messages_count)} model={self.model}"
        )

    def _print_llm_response(
        self,
        *,
        turn_id: str,
        agent_id: str,
        iteration: int,
        response_text: str,
        tool_calls: List[Dict[str, Any]],
    ) -> None:
        if not self.enable_llm_console_log:
            return
        text = str(response_text or "")
        preview = AgentUtils.truncate(" ".join(text.split()), 220)
        names: List[str] = []
        for row in tool_calls[:6]:
            names.append(str(row.get("name", "") or "unknown"))
        tools_preview = ",".join(names) if names else "-"
        if len(tool_calls) > 6:
            tools_preview += ",..."
        print(
            f"[LLM] response turn={turn_id} agent={agent_id} iter={int(iteration)} "
            f"tool_calls={len(tool_calls)} tools={tools_preview} "
            f"content_chars={len(text)} preview={preview}"
        )

    def _print_llm_error(self, *, turn_id: str, agent_id: str, iteration: int, error_text: str) -> None:
        if not self.enable_llm_console_log:
            return
        print(
            f"[LLM] error turn={turn_id} agent={agent_id} iter={int(iteration)} "
            f"error={AgentUtils.truncate(str(error_text or ''), 280)}"
        )

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

    def _make_run_idapython_task_tool(self) -> BaseTool:
        @tool("run_idapython_task", parse_docstring=True, error_on_invalid_docstring=True)
        async def run_idapython_task(goal: str, background: str = "") -> str:
            """Run a goal-driven IDAPython agent workflow.

            Args:
                goal: 目标动作描述（IDAPythonAgent 需要完成的功能）。
                background: 相关上下文背景信息（函数名、EA、约束、已知结论等）。

            Returns:
                纯文本执行结果（默认仅返回执行结果正文）。
            """
            return await self.idapython_agent.run_task(
                goal=goal,
                background=background,
                max_iterations=8,
            )

        return run_idapython_task

    @staticmethod
    def _replace_execute_idapython_tool(tools: List[Any], execute_tool: BaseTool) -> List[Any]:
        return ExpertToolRegistry.replace_execute_idapython_tool(tools, execute_tool)

    def _build_expert_tools(self, profile: str) -> List[Any]:
        execute_tool = self._make_run_idapython_task_tool()
        return ExpertToolRegistry.build_profile_tools(
            profile=profile,
            execute_tool=execute_tool,
            core_tools=list(registered_tools),
            full_tools=list(all_registered_tools),
        )

    def _new_tool_execution_extension(self) -> Any:
        factory = self.tool_execution_extension_factory
        if not callable(factory):
            return None
        try:
            return factory()
        except Exception:
            return None

    def _reset_tool_execution_extension(self) -> None:
        ext = self.tool_execution_extension
        if ext is None:
            return
        reset_fn = getattr(ext, "reset", None)
        if not callable(reset_fn):
            return
        try:
            reset_fn()
        except Exception:
            pass

    def _annotate_tool_result(self, *, tool_name: str, result: str, is_error: bool) -> Dict[str, Any]:
        ext = self.tool_execution_extension
        if ext is None:
            return {}
        annotate_fn = getattr(ext, "annotate_tool_result", None)
        if not callable(annotate_fn):
            return {}
        try:
            payload = annotate_fn(tool_name=tool_name, result=result, is_error=bool(is_error))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def _on_tool_execution_completed(self, row: Dict[str, Any]) -> None:
        ext = self.tool_execution_extension
        if ext is None:
            return
        notify_fn = getattr(ext, "on_tool_result", None)
        if not callable(notify_fn):
            return
        try:
            notify_fn(dict(row))
        except Exception:
            pass

    def _build_session_complete_extension_payload(self) -> Dict[str, Any]:
        ext = self.tool_execution_extension
        if ext is None:
            return {}
        payload_fn = getattr(ext, "build_session_complete_payload", None)
        if not callable(payload_fn):
            return {}
        try:
            payload = payload_fn()
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

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
        self._reset_tool_execution_extension()

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
            final_obj = ToolMessage(
                content=content_text,
                tool_call_id=str(getattr(message_obj, "tool_call_id", "") or ""),
                name=str(getattr(message_obj, "name", "") or "")
            )
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

    @staticmethod
    def _build_tool_observability_row(row: Dict[str, Any]) -> Dict[str, Any]:
        base = {
            "tool_call_id": str(row.get("tool_call_id", "") or ""),
            "tool_name": str(row.get("tool_name", "") or ""),
            "is_error": bool(row.get("is_error", False)),
            "duration_ms": int(row.get("duration_ms", 0) or 0),
            "result_preview": AgentUtils.truncate(str(row.get("result", "") or ""), 600),
        }
        for key, value in row.items():
            if key in {"index", "tool_call_id", "tool_name", "is_error", "result", "duration_ms"}:
                continue
            base[key] = value
        return base

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

            Args:
                view: 视图模式，plan/status/both。
                filter_status: 可选状态过滤条件。

            Returns:
                Markdown 纯文本任务板。
            """
            return self.task_board.get_task_board(view=view, filter_status=filter_status)

        @tool("prune_context_messages", parse_docstring=True, error_on_invalid_docstring=True)
        def prune_context_messages(remove_message_ids: str = "", fold_message_ids: str = "", reason: str = "") -> str:
            """Fold policy messages by Message_xxx IDs.

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
        async def spawn_subagent(task: str, profile: str = "general", context: str = "", priority: str = "normal") -> str:
            """Run one subagent task and return its final output.

            Args:
                task: 子任务说明（Markdown 纯文本）。
                profile: 子 Agent 配置名称，默认 general。
                context: 额外上下文，建议包含证据与约束。
                priority: 优先级标签，默认 normal。

            Returns:
                子 Agent 执行完成后的纯文本结果（前缀为 OK: 或 ERROR:）。
            """
            return await self.subagent_runtime.spawn_subagent(
                task=task,
                profile=profile,
                context=context,
                priority=priority,
                current_agent_id=current_agent_id,
                user_request=user_request,
            )

        @tool("submit_output", parse_docstring=True, error_on_invalid_docstring=True)
        def submit_output(summary: str, key_findings: str = "", artifacts: str = "", next_steps: str = "") -> str:
            """Submit final output and end the main policy loop.

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
                lines.append("- action: wait for spawn_subagent results before submit_output")
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
        finalize_tool_names = {"submit_output", "submit_subagent_output", "submit_idapython_result"}

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
            annotation = self._annotate_tool_result(
                tool_name=tool_name,
                result=result,
                is_error=is_error,
            )
            return {
                "index": int(idx),
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "is_error": is_error,
                "result": result,
                "duration_ms": int((time.perf_counter() - one_started) * 1000),
                **annotation,
            }

        normal_calls: List[tuple[int, Dict[str, Any]]] = []
        finalize_calls: List[tuple[int, Dict[str, Any]]] = []
        for idx, call in enumerate(tool_calls, start=1):
            name = str(call.get("name", "") or "")
            if name in finalize_tool_names:
                finalize_calls.append((idx, call))
            else:
                normal_calls.append((idx, call))

        outputs: List[Dict[str, Any]] = []
        if normal_calls:
            jobs = [
                asyncio.create_task(_run_one(idx, call))
                for idx, call in normal_calls
            ]
            outputs.extend(await asyncio.gather(*jobs))
        for idx, call in finalize_calls:
            outputs.append(await _run_one(idx, call))
        outputs = sorted(outputs, key=lambda row: int(row.get("index", 0)))

        for row in outputs:
            result_text = str(row.get("result", "") or "")
            self._on_tool_execution_completed(row)
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
                "finalize_count": len(finalize_calls),
                "success_count": len([row for row in outputs if not bool(row.get("is_error", False))]),
                "error_count": len([row for row in outputs if bool(row.get("is_error", False))]),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "durations_ms": [int(row.get("duration_ms", 0)) for row in outputs],
                "tool_calls": [self._build_tool_observability_row(row) for row in outputs],
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
            "# Reverse Agent Final Summary",
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
            "# Reverse Agent Incomplete",
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
            self._print_llm_request(
                turn_id=turn_id,
                agent_id=agent_id,
                iteration=iteration,
                messages_count=len(messages),
            )
            try:
                response = await self._ainvoke_with_retry(llm_with_tools, messages, max_retries=self.llm_max_retries)
            except Exception as e:
                self._print_llm_error(
                    turn_id=turn_id,
                    agent_id=agent_id,
                    iteration=iteration,
                    error_text=str(e),
                )
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
            self._print_llm_response(
                turn_id=turn_id,
                agent_id=agent_id,
                iteration=iteration,
                response_text=response_text,
                tool_calls=tool_calls,
            )
            ai_message = AIMessage(content=response_text, tool_calls=tool_calls)
            self.policy_mgr.append_message(
                messages=messages,
                message_obj=ai_message,
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
                    tool_name = str(row.get("tool_name", "") or call_rows[idx - 1].get("name", "") or "")
                    self.policy_mgr.append_message(
                        messages=messages,
                        message_obj=ToolMessage(content=result_text, tool_call_id=tool_call_id, name=tool_name),
                        role="tool",
                        turn_id=turn_id,
                        protected=False,
                    )
            latency_s = time.perf_counter() - interaction_started

            # Extract usage metadata from response
            usage_metadata = getattr(response, "usage_metadata", None) or {}
            usage = {}
            if usage_metadata:
                usage["input_tokens"] = usage_metadata.get("input_tokens") or usage_metadata.get("prompt_tokens")
                usage["output_tokens"] = usage_metadata.get("output_tokens") or usage_metadata.get("completion_tokens")

            # 记录完整的消息历史，包括 system prompt 和之前的对话
            self.obs.emit(
                "llm_interaction",
                {
                    "turn_id": turn_id,
                    "agent_id": agent_id,
                    "agent_name": self.runtime_name,
                    "iteration": iteration,
                    "phase": "main" if agent_id == "main" else "subagent",
                    "messages": self._serialize_messages_for_log(messages),
                    "latency_s": round(latency_s, 4),
                    "usage": usage if usage.get("input_tokens") or usage.get("output_tokens") else None,
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

        # Set binary name for this session
        if self.session_logger:
            try:
                binary_name = self.ida_client.get_current_filename()
                if binary_name:
                    self.session_logger.set_binary_name(binary_name)
                    print(f"[INFO] Session binary: {binary_name}")
            except Exception as e:
                print(f"[WARN] Failed to get binary name: {e}")

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
            completion_payload = {
                "duration_s": round(time.perf_counter() - run_started, 4),
                "iterations_used": iterations_used,
                "final_response": AgentUtils.truncate(final_text, 12000),
                "completed_reason": str(result.get("reason", "")),
                "policy_id": self.policy_id,
                "git_commit": self.git_commit,
                "loop_mode": self.loop_mode,
            }
            completion_payload.update(self._build_session_complete_extension_payload())
            self.obs.emit(
                event_name,
                completion_payload,
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

class StructRecoveryAgentCore:
    """Struct recovery specialized facade."""

    def __init__(
        self,
        ida_service_url: str = "http://127.0.0.1:5000",
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        model: Optional[str] = None,
        prompt_root: Optional[str] = None,
        agent_depth: int = 0,
        idapython_kb_dir: str = "",
        tool_profile: str = "struct_recovery",
        runtime_name: str = "StructRecoveryAgentCore",
        tool_execution_extension_factory: Optional[Callable[[], Any]] = None,
    ):
        extension_factory = tool_execution_extension_factory or StructRecoveryToolExecutionExtension
        self._core = StructRecoveryRuntimeCore(
            ida_service_url=ida_service_url,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            model=model,
            prompt_root=prompt_root,
            agent_depth=agent_depth,
            idapython_kb_dir=idapython_kb_dir,
            tool_profile=tool_profile,
            runtime_name=runtime_name,
            tool_execution_extension_factory=extension_factory,
        )

    @property
    def ida_client(self):
        return self._core.ida_client

    async def run(self, user_request: str, max_iterations: int = 30) -> str:
        return await self._core.run(user_request=user_request, max_iterations=max_iterations)

    def get_last_session_id(self) -> Optional[str]:
        return self._core.get_last_session_id()

    def get_session_db_path(self) -> Optional[str]:
        return self._core.get_session_db_path()

    def log_runtime_event(self, event: str, payload):
        self._core.log_runtime_event(event, payload)

    def __getattr__(self, name: str):
        return getattr(self._core, name)
