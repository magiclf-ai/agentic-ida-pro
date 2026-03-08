"""Sub-agent runtime orchestration for StructRecoveryRuntimeCore."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .models import SubAgentState
from .utils import AgentUtils

if TYPE_CHECKING:
    from .struct_recovery_agent import StructRecoveryRuntimeCore


class SubAgentRuntime:
    """Owns sub-agent lifecycle and policy loop execution."""

    def __init__(self, core: "StructRecoveryRuntimeCore"):
        self.core = core

    async def spawn_subagent(
        self,
        *,
        task: str,
        profile: str,
        context: str,
        priority: str,
        current_agent_id: str,
        user_request: str,
    ) -> str:
        if int(self.core.agent_depth) >= int(self.core.max_subagent_depth):
            return (
                "ERROR: subagent depth limit reached\n"
                f"- current_depth: {int(self.core.agent_depth)}\n"
                f"- max_subagent_depth: {int(self.core.max_subagent_depth)}"
            )
        task_md = str(task or "").strip()
        if not task_md:
            return "ERROR: missing task"
        sub_id = f"{current_agent_id}.s{len(self.core.subagent_mgr.all_states()) + 1}_{uuid.uuid4().hex[:6]}"
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
        self.core.subagent_mgr.register(state)
        self.core.obs.emit(
            "subagent_spawned",
            {
                "agent_id": sub_id,
                "parent_agent_id": current_agent_id,
                "profile": state.profile,
                "priority": state.priority,
                "task_md": AgentUtils.truncate(state.task_md, 2400),
                "context_md": AgentUtils.truncate(state.context_md, 1800),
                "policy_id": self.core.policy_id,
                "git_commit": self.core.git_commit,
                "loop_mode": self.core.loop_mode,
                "mode": "sync_wait",
            },
        )
        try:
            if self.core._subagent_sem is None:
                self.core._subagent_sem = asyncio.Semaphore(self.core.subagent_max_parallel)
            async with self.core._subagent_sem:
                result = await self.run_single_subagent(
                    subagent_id=sub_id,
                    parent_agent_id=current_agent_id,
                    request={
                        "profile": state.profile,
                        "priority": state.priority,
                        "task": state.task_md,
                        "context": state.context_md,
                    },
                    user_request=user_request,
                    max_iterations=max(1, int(self.core.subagent_default_max_iterations)),
                )

            state.status = "completed" if bool(result.get("completed", True)) else "incomplete"
            state.result_md = str(result.get("output", "") or "")
            state.error_text = ""
            state.delivered_to_parent = True
            state.updated_at = time.time()
            self.core.obs.emit(
                "subagent_result_received",
                {
                    "agent_id": state.agent_id,
                    "parent_agent_id": state.parent_agent_id,
                    "status": state.status,
                    "reason": str(result.get("reason", "") or ""),
                    "error": state.error_text,
                    "result_preview": AgentUtils.truncate(state.result_md, 1200),
                    "policy_id": self.core.policy_id,
                    "git_commit": self.core.git_commit,
                    "loop_mode": self.core.loop_mode,
                },
            )
            return (
                f"OK: subagent completed subagent_id={sub_id}\n"
                f"- status: {state.status}\n"
                f"- profile: {state.profile}\n"
                f"- priority: {state.priority}\n"
                f"- reason: {str(result.get('reason', '') or '') or '(none)'}\n"
                "- result:\n"
                + (state.result_md or "(empty subagent result)")
            )
        except Exception as e:
            state.status = "failed"
            state.error_text = str(e)
            state.result_md = f"ERROR: subagent failed: {e}"
            state.delivered_to_parent = True
            state.updated_at = time.time()
            self.core.obs.emit(
                "subagent_result_received",
                {
                    "agent_id": state.agent_id,
                    "parent_agent_id": state.parent_agent_id,
                    "status": state.status,
                    "error": state.error_text,
                    "result_preview": AgentUtils.truncate(state.result_md, 1200),
                    "policy_id": self.core.policy_id,
                    "git_commit": self.core.git_commit,
                    "loop_mode": self.core.loop_mode,
                },
            )
            return state.result_md

    async def run_single_subagent(
        self,
        *,
        subagent_id: str,
        parent_agent_id: str,
        request: Dict[str, Any],
        user_request: str,
        max_iterations: int,
    ) -> Dict[str, Any]:
        profile_name = str(request.get("profile", "general") or "general").strip() or "general"
        if int(self.core.agent_depth) + 1 > int(self.core.max_subagent_depth):
            return {
                "subagent_id": subagent_id,
                "profile": profile_name,
                "priority": str(request.get("priority", "normal") or "normal"),
                "task": str(request.get("task", "") or ""),
                "completed": False,
                "reason": "depth_limit_reached",
                "output": (
                    "ERROR: subagent depth limit reached\n"
                    f"- current_depth: {int(self.core.agent_depth)}\n"
                    f"- max_subagent_depth: {int(self.core.max_subagent_depth)}"
                ),
            }

        child = self.spawn_isolated_subagent_core()
        started = time.perf_counter()
        result = await child.subagent_runtime.run_subagent_policy_loop(
            user_request=str(user_request or "").strip(),
            request=request,
            max_iterations=max(1, int(max_iterations)),
            agent_id=subagent_id,
            parent_agent_id=parent_agent_id,
        )
        latency = time.perf_counter() - started
        output = str(result.get("final_text", "") or "").strip() or "(empty subagent output)"

        self.core.obs.emit(
            "subagent_plan_generated",
            {
                "agent_id": subagent_id,
                "parent_agent_id": parent_agent_id,
                "profile": profile_name,
                "priority": str(request.get("priority", "normal") or "normal"),
                "task_md": AgentUtils.truncate(str(request.get("task", "") or ""), 2400),
                "output_preview": AgentUtils.truncate(output, 1600),
                "latency_s": round(latency, 4),
                "policy_id": self.core.policy_id,
                "git_commit": self.core.git_commit,
                "loop_mode": self.core.loop_mode,
            },
        )
        return {
            "subagent_id": subagent_id,
            "profile": profile_name,
            "priority": str(request.get("priority", "normal") or "normal"),
            "task": str(request.get("task", "") or ""),
            "completed": bool(result.get("completed", False)),
            "reason": str(result.get("reason", "") or ""),
            "iterations_used": int(result.get("iterations_used", 0) or 0),
            "output": output,
        }

    def spawn_isolated_subagent_core(self) -> "StructRecoveryRuntimeCore":
        child_cls = self.core.__class__
        child = child_cls(
            ida_service_url=self.core.ida_client.base_url,
            openai_api_key=self.core._openai_api_key,
            openai_base_url=self.core._openai_base_url,
            model=self.core.model,
            prompt_root=str(self.core.prompt_manager.prompt_root),
            agent_depth=int(self.core.agent_depth) + 1,
            idapython_kb_dir=self.core.idapython_kb_dir,
            tool_profile=self.core.tool_profile,
            runtime_name=self.core.runtime_name,
            tool_execution_extension_factory=self.core.tool_execution_extension_factory,
        )
        # Share parent's session logger so subagent logs go to same database.
        child.enable_session_log = self.core.enable_session_log
        child.session_logger = self.core.session_logger
        child.last_session_id = self.core.last_session_id
        child.session_db_path = self.core.session_db_path
        child.obs = self.core.obs
        child.tool_result_log_chars = self.core.tool_result_log_chars
        child.message_log_chars = self.core.message_log_chars
        child.llm_max_retries = self.core.llm_max_retries
        child.subagent_max_retries = self.core.subagent_max_retries
        child.tool_call_max_parallel = self.core.tool_call_max_parallel
        child.tool_call_collect_all_errors = self.core.tool_call_collect_all_errors
        child.tool_profile = self.core.tool_profile
        child.expert_tools = child._build_expert_tools(child.tool_profile)
        child.expert_tool_map = {row.name: row for row in child.expert_tools}
        child.context_window_messages = self.core.context_window_messages
        child.subagent_max_context_chars = self.core.subagent_max_context_chars
        child.subagent_max_parallel = self.core.subagent_max_parallel
        child.policy_history_max_messages = self.core.policy_history_max_messages
        child.policy_history_max_chars = self.core.policy_history_max_chars
        child.policy_history_soft_ratio = self.core.policy_history_soft_ratio
        child.policy_history_keep_tail_messages = self.core.policy_history_keep_tail_messages
        child.policy_history_distill_max_messages = self.core.policy_history_distill_max_messages
        child.context_fold_placeholder = self.core.context_fold_placeholder
        child.max_subagent_depth = self.core.max_subagent_depth
        child.subagent_default_max_iterations = self.core.subagent_default_max_iterations
        child.idapython_kb_dir = self.core.idapython_kb_dir
        return child

    async def run_subagent_policy_loop(
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
            system_prompt = self.core.prompt_manager.render(template_name, {"profile_name": profile_name})
        except FileNotFoundError:
            system_prompt = self.core.prompt_manager.render("subagents/general.md", {"profile_name": profile_name})

        human_prompt = self.core.prompt_manager.render(
            "agent/subagent_user.md",
            {
                "user_request": str(user_request or ""),
                "task": str(request.get("task", "") or "").strip(),
                "context": AgentUtils.truncate(str(request.get("context", "") or "").strip(), self.core.subagent_max_context_chars),
            },
        )

        max_iters = max(1, int(max_iterations))
        messages: List[Any] = []
        init_turn_id = f"{agent_id}:0:subpolicy:init"
        self.core.policy_mgr.append_message(
            messages=messages,
            message_obj=SystemMessage(content=system_prompt),
            role="system",
            turn_id=init_turn_id,
            protected=True,
        )
        self.core.policy_mgr.append_message(
            messages=messages,
            message_obj=HumanMessage(content=human_prompt),
            role="user",
            turn_id=init_turn_id,
            protected=True,
        )
        for iteration in range(1, max_iters + 1):
            turn_id = f"{agent_id}:{iteration}:subpolicy:{uuid.uuid4().hex[:8]}"
            subagent_updates = self.core.subagent_mgr.drain_completed_updates(parent_agent_id=agent_id)
            if subagent_updates:
                self.core.policy_mgr.append_message(
                    messages=messages,
                    message_obj=HumanMessage(content=subagent_updates),
                    role="user",
                    turn_id=turn_id,
                    protected=False,
                )
            compression_prompt = await self.core._maybe_compress_policy_history(
                messages=messages,
                iteration=iteration,
                user_request=user_request,
                agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                turn_id=turn_id,
            )
            if str(compression_prompt or "").strip():
                self.core.policy_mgr.append_message(
                    messages=messages,
                    message_obj=HumanMessage(content=str(compression_prompt).strip()),
                    role="user",
                    turn_id=turn_id,
                    protected=False,
                )

            include_context_tools = self.core._should_expose_context_tools(messages=messages, compression_prompt=compression_prompt)
            runtime_tools = self.core._make_runtime_tools(
                current_agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                user_request=user_request,
                max_iterations=max_iters,
                include_context_tools=include_context_tools,
                finalize_mode="subagent",
            )
            all_tools = list(self.core.expert_tools) + list(runtime_tools)
            tool_map = {tool_obj.name: tool_obj for tool_obj in all_tools}
            llm_with_tools = self.core.llm.bind_tools(all_tools)
            self.core.obs.emit(
                "tools_bound",
                {
                    "turn_id": turn_id,
                    "agent_id": agent_id,
                    "tools": [{"name": t.name, "description": getattr(t, "description", "")} for t in all_tools],
                },
            )

            interaction_started = time.perf_counter()
            response = await self.core._ainvoke_with_retry(llm_with_tools, messages, max_retries=self.core.llm_max_retries)
            response_content = getattr(response, "content", "") or ""
            response_text = AgentUtils.content_to_text(response_content)
            tool_calls = self.core._normalize_tool_calls(getattr(response, "tool_calls", None), turn_id=turn_id)
            self.core.policy_mgr.append_message(
                messages=messages,
                message_obj=AIMessage(content=response_text, tool_calls=tool_calls),
                role="assistant",
                turn_id=turn_id,
                protected=False,
            )

            assistant_text = response_text.strip()
            if assistant_text:
                self.core.context_mgr.append(
                    role="assistant",
                    source="llm:subpolicy",
                    content=assistant_text,
                    turn_id=turn_id,
                    agent_id=agent_id,
                )

            call_rows = tool_calls if isinstance(tool_calls, list) else []
            if call_rows:
                outputs = await self.core._execute_tool_calls(
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
                    self.core.policy_mgr.append_message(
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

            self.core.obs.emit(
                "llm_interaction",
                {
                    "turn_id": turn_id,
                    "agent_id": agent_id,
                    "agent_name": self.core.__class__.__name__,
                    "iteration": iteration,
                    "phase": "subagent",
                    "messages": self.core._serialize_messages_for_log(messages),
                    "latency_s": round(latency_s, 4),
                    "usage": usage if usage.get("input_tokens") or usage.get("output_tokens") else None,
                },
            )
            if self.core._finalized:
                return {
                    "final_text": self.core._final_text or self.core._build_subagent_final_text(),
                    "iterations_used": iteration,
                    "completed": True,
                    "reason": "submit_subagent_output_called",
                }

        return {
            "final_text": self.core._build_subagent_incomplete_text(iteration=max_iters, max_iterations=max_iters),
            "iterations_used": max_iters,
            "completed": False,
            "reason": "max_iterations_reached",
        }
