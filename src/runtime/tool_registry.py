"""Unified tool registry for agent tool profiles."""
from __future__ import annotations

from typing import Any, List

from langchain_core.tools import BaseTool

from core.tools import get_tools_for_profile, normalize_tool_profile


class ExpertToolRegistry:
    """Build tool sets by profile and keep script-task entry replacement centralized."""

    @staticmethod
    def normalize_profile(profile: str) -> str:
        return normalize_tool_profile(profile)

    @staticmethod
    def replace_execute_idapython_tool(
        tools: List[Any],
        execute_tool: BaseTool,
        *,
        insert_if_missing: bool = False,
    ) -> List[Any]:
        replaced: List[Any] = []
        swapped = False
        for tool_obj in tools:
            if str(getattr(tool_obj, "name", "") or "") == "execute_idapython":
                if not swapped:
                    replaced.append(execute_tool)
                    swapped = True
                continue
            replaced.append(tool_obj)
        if insert_if_missing and (not swapped):
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
        del core_tools
        mode = ExpertToolRegistry.normalize_profile(profile)
        if mode == "execute_only":
            return [execute_tool]
        selected = get_tools_for_profile(mode, available_tools=list(full_tools))
        has_execute = any(str(getattr(tool_obj, "name", "") or "") == "execute_idapython" for tool_obj in selected)
        return ExpertToolRegistry.replace_execute_idapython_tool(
            selected,
            execute_tool,
            insert_if_missing=has_execute,
        )
