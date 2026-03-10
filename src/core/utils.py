"""Utility functions for the expert agent."""
from __future__ import annotations

import json
import subprocess
from typing import Any, List


class AgentUtils:
    """Utility functions for agent operations."""

    @staticmethod
    def git_commit() -> str:
        """Get current git commit hash.

        Returns:
            Short commit hash or 'unknown'
        """
        try:
            out = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL
            )
            return out.decode("utf-8", errors="ignore").strip() or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def content_to_text(content: Any) -> str:
        """Convert message content to text.

        Args:
            content: Content to convert

        Returns:
            Text representation
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            out: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        out.append(str(item.get("text", "")))
                    elif item.get("type") == "text":
                        out.append(str(item.get("text", "")))
                    else:
                        out.append(json.dumps(item, ensure_ascii=False, default=str))
                else:
                    out.append(str(item))
            return "\n".join(out)
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False, default=str)
        return str(content or "")

    @staticmethod
    def truncate(text: Any, max_chars: int) -> str:
        """Truncate text to maximum characters.

        Args:
            text: Text to truncate
            max_chars: Maximum characters

        Returns:
            Truncated text
        """
        value = AgentUtils.content_to_text(text)
        if len(value) <= int(max_chars):
            return value
        return value[:int(max_chars)] + f"... [truncated {len(value) - int(max_chars)} chars]"

    @staticmethod
    def has_runtime_error_marker(text: Any) -> bool:
        """Check if text contains runtime error markers.

        Args:
            text: Text to check

        Returns:
            True if error markers found
        """
        value = str(text or "")
        return "[ERROR]" in value or "Traceback (most recent call last):" in value

    @staticmethod
    def find_destructive_struct_ops(script: str) -> List[str]:
        """Find destructive structure operations in script.

        Args:
            script: Script text to analyze

        Returns:
            List of destructive operations found
        """
        text = str(script or "").lower()
        patterns = [
            "idc.del_struc(",
            "del_struc(",
        ]
        hits: List[str] = []
        for pattern in patterns:
            if pattern in text:
                hits.append(pattern.rstrip("("))
        return sorted(set(hits))
