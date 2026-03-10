"""Struct-recovery specializations over the generic reverse runtime."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from runtime import ReverseRuntimeCore, ReverseToolExecutionExtension


class StructRecoveryToolExecutionExtension(ReverseToolExecutionExtension):
    """Struct-recovery mutation tracking configuration."""

    def __init__(self) -> None:
        super().__init__(
            mutating_tool_names={
                "create_structure",
                "set_identifier_type",
                "set_function_comment",
            },
            type_application_tool_names={"set_identifier_type"},
        )


class StructRecoveryRuntimeCore(ReverseRuntimeCore):
    """Struct-recovery runtime configuration."""

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
        runtime_name: str = "StructRecoveryRuntimeCore",
        tool_execution_extension_factory: Optional[Callable[[], Any]] = None,
    ):
        extension_factory = tool_execution_extension_factory or StructRecoveryToolExecutionExtension
        super().__init__(
            ida_service_url=ida_service_url,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            model=model,
            prompt_root=prompt_root,
            agent_depth=agent_depth,
            idapython_kb_dir=idapython_kb_dir,
            tool_profile=tool_profile or "struct_recovery",
            agent_profile="struct_recovery",
            runtime_name=runtime_name,
            tool_execution_extension_factory=extension_factory,
            finalize_tool_name="submit_output",
            system_prompt_path="agent/reverse_expert_system.md",
        )


class StructRecoveryAgentCore:
    """Struct-recovery facade."""

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

    def log_runtime_event(self, event: str, payload: Dict[str, Any]) -> None:
        self._core.log_runtime_event(event, payload)

    def __getattr__(self, name: str):
        return getattr(self._core, name)
