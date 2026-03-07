"""Unified tool registry for agent tool profiles."""
from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.tools import BaseTool


class ExpertToolRegistry:
    """Build tool sets by profile and keep script-task entry replacement centralized."""

    PROFILE_ALIASES: Dict[str, str] = {
        "": "execute_only",
        "minimal_codeact": "execute_only",
        "codeact": "execute_only",
        "execute_only": "execute_only",
        "struct_recovery": "struct_recovery",
        "full": "full",
        "full_tools": "full",
    }

    @staticmethod
    def normalize_profile(profile: str) -> str:
        key = str(profile or "").strip().lower()
        return ExpertToolRegistry.PROFILE_ALIASES.get(key, "execute_only")

    @staticmethod
    def replace_execute_idapython_tool(tools: List[Any], execute_tool: BaseTool) -> List[Any]:
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

    @staticmethod
    def build_profile_tools(
        *,
        profile: str,
        execute_tool: BaseTool,
        core_tools: List[Any],
        full_tools: List[Any],
    ) -> List[Any]:
        mode = ExpertToolRegistry.normalize_profile(profile)
        if mode == "execute_only":
            return [execute_tool]
        if mode == "struct_recovery":
            return ExpertToolRegistry.replace_execute_idapython_tool(list(core_tools), execute_tool)
        if mode == "full":
            return ExpertToolRegistry.replace_execute_idapython_tool(list(full_tools), execute_tool)
        return [execute_tool]
