"""Reverse agent dispatcher core.

This core only routes requests to specialized agent cores.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .struct_recovery_agent import StructRecoveryAgentCore


class ReverseAgentCore:
    """Task dispatcher that routes reverse requests to specialized agent cores."""

    REQUIRED_MODEL = "gpt-5.2"
    SUPPORTED_AGENT_CORES = {"struct_recovery"}

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
        runtime_name: str = "ReverseAgentCore",
        agent_core: str = "struct_recovery",
        tool_execution_extension_factory=None,
    ):
        self.runtime_name = str(runtime_name or "").strip() or "ReverseAgentCore"
        self.tool_profile = str(tool_profile or "execute_only").strip() or "execute_only"
        requested_core = str(agent_core or "").strip().lower() or "struct_recovery"
        if requested_core not in self.SUPPORTED_AGENT_CORES:
            raise ValueError(
                f"Unsupported agent_core '{requested_core}'. "
                f"Supported values: {sorted(self.SUPPORTED_AGENT_CORES)}"
            )
        self.agent_core = requested_core

        # Current dispatcher has one specialized runtime: struct recovery.
        self._struct_recovery_agent = StructRecoveryAgentCore(
            ida_service_url=ida_service_url,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            model=model,
            prompt_root=prompt_root,
            agent_depth=agent_depth,
            idapython_kb_dir=idapython_kb_dir,
            tool_profile="struct_recovery" if self.tool_profile == "execute_only" else self.tool_profile,
            runtime_name="StructRecoveryAgentCore",
            tool_execution_extension_factory=tool_execution_extension_factory,
        )

    @property
    def ida_client(self):
        return self._struct_recovery_agent.ida_client

    def _select_agent(self):
        if self.agent_core == "struct_recovery":
            return self._struct_recovery_agent
        raise ValueError(f"Unsupported agent_core '{self.agent_core}'")

    async def run(self, user_request: str, max_iterations: int = 30) -> str:
        selected = self._select_agent()
        try:
            selected.log_runtime_event(
                "dispatcher_route",
                {
                    "dispatcher": self.runtime_name,
                    "selected_agent": self.agent_core,
                    "request_preview": str(user_request or "")[:600],
                },
            )
        except Exception:
            pass
        return await selected.run(user_request=user_request, max_iterations=max_iterations)

    def get_last_session_id(self) -> Optional[str]:
        return self._struct_recovery_agent.get_last_session_id()

    def get_session_db_path(self) -> Optional[str]:
        return self._struct_recovery_agent.get_session_db_path()

    def log_runtime_event(self, event: str, payload: Dict[str, Any]) -> None:
        self._struct_recovery_agent.log_runtime_event(event, payload)

    def __getattr__(self, name: str):
        return getattr(self._struct_recovery_agent, name)
