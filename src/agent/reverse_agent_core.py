"""Reverse agent dispatcher core."""
from __future__ import annotations

from typing import Any, Dict, Optional

from runtime import ReverseRuntimeCore
from core.tools import get_finalize_config_for_profile, normalize_tool_profile


class ReverseAgentCore:
    """Profile dispatcher that routes reverse requests to the generic runtime."""

    SUPPORTED_PROFILES = {
        "struct_recovery",
        "attack_surface",
        "general_reverse",
    }

    def __init__(
        self,
        ida_service_url: str = "http://127.0.0.1:5000",
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        model: Optional[str] = None,
        prompt_root: Optional[str] = None,
        agent_depth: int = 0,
        idapython_kb_dir: str = "",
        runtime_name: str = "ReverseAgentCore",
        agent_profile: str = "struct_recovery",
        tool_profile: str = "",
        tool_execution_extension_factory=None,
    ):
        self.runtime_name = str(runtime_name or "").strip() or "ReverseAgentCore"
        requested_profile = normalize_tool_profile(agent_profile or "struct_recovery")
        if requested_profile not in self.SUPPORTED_PROFILES:
            raise ValueError(
                f"Unsupported agent_profile '{requested_profile}'. "
                f"Supported values: {sorted(self.SUPPORTED_PROFILES)}"
            )
        self.agent_profile = requested_profile
        finalize_config = get_finalize_config_for_profile(requested_profile)

        self._runtime = ReverseRuntimeCore(
            ida_service_url=ida_service_url,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            model=model,
            prompt_root=prompt_root,
            agent_depth=agent_depth,
            idapython_kb_dir=idapython_kb_dir,
            tool_profile=tool_profile or requested_profile,
            agent_profile=requested_profile,
            runtime_name=f"{requested_profile.title().replace('_', '')}AgentCore",
            finalize_tool_name=str(finalize_config.get("tool_name", "") or "submit_output"),
            tool_execution_extension_factory=tool_execution_extension_factory,
        )

    @property
    def ida_client(self):
        return self._runtime.ida_client

    async def run(self, user_request: str, max_iterations: int = 30) -> str:
        try:
            self._runtime.log_runtime_event(
                "dispatcher_route",
                {
                    "dispatcher": self.runtime_name,
                    "selected_profile": self.agent_profile,
                    "request_preview": str(user_request or "")[:600],
                },
            )
        except Exception:
            pass
        return await self._runtime.run(user_request=user_request, max_iterations=max_iterations)

    def get_last_session_id(self) -> Optional[str]:
        return self._runtime.get_last_session_id()

    def get_session_db_path(self) -> Optional[str]:
        return self._runtime.get_session_db_path()

    def log_runtime_event(self, event: str, payload: Dict[str, Any]) -> None:
        self._runtime.log_runtime_event(event, payload)

    def __getattr__(self, name: str):
        return getattr(self._runtime, name)
