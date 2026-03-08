"""Sub-agent manager for handling child agent execution."""
from __future__ import annotations

import time
from typing import Dict, List, Optional, TYPE_CHECKING

from .models import SubAgentState

if TYPE_CHECKING:
    from .observability import ObservabilityHub


class SubAgentManager:
    """Manages sub-agent lifecycle and result delivery."""

    def __init__(self, obs: Optional[ObservabilityHub] = None):
        self._subagents: Dict[str, SubAgentState] = {}
        self.obs = obs

    def reset(self) -> None:
        """Reset all sub-agent state."""
        self._subagents.clear()

    def register(self, state: SubAgentState) -> None:
        """Register a new sub-agent.

        Args:
            state: Sub-agent state to register
        """
        self._subagents[state.agent_id] = state

    def get(self, agent_id: str) -> Optional[SubAgentState]:
        """Get sub-agent state by ID.

        Args:
            agent_id: Agent ID

        Returns:
            Sub-agent state or None
        """
        return self._subagents.get(agent_id)

    def update_status(self, agent_id: str, status: str, result_md: str = "", error_text: str = "") -> None:
        """Update sub-agent status.

        Args:
            agent_id: Agent ID
            status: New status
            result_md: Result markdown (optional)
            error_text: Error text (optional)
        """
        state = self._subagents.get(agent_id)
        if not state:
            return
        state.status = status
        state.updated_at = time.time()
        if result_md:
            state.result_md = result_md
        if error_text:
            state.error_text = error_text

    def pending_for_parent(self, parent_agent_id: str) -> List[SubAgentState]:
        """Get pending sub-agents for a parent.

        Args:
            parent_agent_id: Parent agent ID

        Returns:
            List of pending sub-agent states
        """
        parent_id = str(parent_agent_id or "").strip()
        if not parent_id:
            return []
        rows: List[SubAgentState] = []
        for state in self._subagents.values():
            if state.parent_agent_id != parent_id:
                continue
            if state.status == "running":
                rows.append(state)
        return rows

    def drain_completed_updates(
        self,
        parent_agent_id: str,
        *,
        truncate_fn=None,
        policy_id: str = "",
        git_commit: str = "",
        loop_mode: str = ""
    ) -> str:
        """Drain completed sub-agent results for parent.

        Args:
            parent_agent_id: Parent agent ID
            truncate_fn: Function to truncate text (optional)
            policy_id: Policy ID for logging
            git_commit: Git commit for logging
            loop_mode: Loop mode for logging

        Returns:
            Markdown formatted results
        """
        parent_id = str(parent_agent_id or "").strip()
        if not parent_id:
            return ""

        def _truncate(text, max_chars):
            if truncate_fn:
                return truncate_fn(text, max_chars)
            value = str(text or "")
            if len(value) <= max_chars:
                return value
            return value[:max_chars] + f"... [truncated {len(value) - max_chars} chars]"

        delivered: List[SubAgentState] = []
        for state in self._subagents.values():
            if state.parent_agent_id != parent_id:
                continue
            if state.status == "running":
                continue
            if state.delivered_to_parent:
                continue
            state.delivered_to_parent = True
            state.updated_at = time.time()
            delivered.append(state)

            if self.obs:
                self.obs.emit(
                    "subagent_result_delivered",
                    {
                        "agent_id": state.agent_id,
                        "parent_agent_id": state.parent_agent_id,
                        "status": state.status,
                        "profile": state.profile,
                        "priority": state.priority,
                        "task_md": _truncate(state.task_md, 800),
                        "result_preview": _truncate(state.result_md, 1200),
                        "policy_id": policy_id,
                        "git_commit": git_commit,
                        "loop_mode": loop_mode,
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
                    f"- task: {state.task_md}",
                    "- result:",
                    (state.result_md or "(empty subagent result)"),
                    "",
                ]
            )
        return "\n".join(lines).strip()

    def all_states(self) -> List[SubAgentState]:
        """Get all sub-agent states.

        Returns:
            List of all sub-agent states
        """
        return list(self._subagents.values())
