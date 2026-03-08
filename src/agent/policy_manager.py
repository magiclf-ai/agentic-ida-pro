"""Policy message history manager."""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .models import PolicyMessageRef


class PolicyManager:
    """Manages policy message history and compression."""

    def __init__(self, context_fold_placeholder: str = "内容已经折叠，信息如需要请重新获取。"):
        self._policy_messages_by_id: Dict[str, PolicyMessageRef] = {}
        self._policy_message_order: List[str] = []
        self._policy_message_obj_to_id: Dict[int, str] = {}
        self._policy_seq: int = 0
        self.context_fold_placeholder = context_fold_placeholder

    def reset(self) -> None:
        """Reset all policy tracking state."""
        self._policy_messages_by_id.clear()
        self._policy_message_order.clear()
        self._policy_message_obj_to_id.clear()
        self._policy_seq = 0

    def next_message_id(self) -> str:
        """Generate next message ID."""
        self._policy_seq += 1
        return f"Message_{self._policy_seq:06d}"

    @staticmethod
    def _content_to_text(content: Any) -> str:
        """Convert message content to text."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            import json
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
            import json
            return json.dumps(content, ensure_ascii=False, default=str)
        return str(content or "")

    def inject_message_id_text(self, content: Any, message_id: str) -> str:
        """Inject message ID into content text.

        Args:
            content: Message content
            message_id: Message ID to inject

        Returns:
            Content with message ID prefix
        """
        prefix = f"消息ID: {str(message_id or '').strip()}"
        text = self._content_to_text(content)
        if text.startswith(prefix):
            return text
        lines = text.splitlines()
        if lines and re.match(r"^\s*消息ID:\s*Message_\d+\s*$", lines[0].strip()):
            text = "\n".join(lines[1:])
        body = str(text or "").lstrip("\n")
        if body:
            return f"{prefix}\n{body}"
        return prefix

    def append_message(
        self,
        *,
        messages: List[Any],
        message_obj: Any,
        role: str,
        turn_id: str,
        protected: bool = False,
    ) -> Any:
        """Append a message to policy history.

        Args:
            messages: Message list to append to
            message_obj: Message object
            role: Message role
            turn_id: Turn ID
            protected: Whether message is protected from compression

        Returns:
            The final message object
        """
        message_id = self.next_message_id()
        content_text = self.inject_message_id_text(getattr(message_obj, "content", ""), message_id)

        if isinstance(message_obj, SystemMessage):
            final_obj: Any = SystemMessage(content=content_text)
        elif isinstance(message_obj, HumanMessage):
            final_obj = HumanMessage(content=content_text)
        elif isinstance(message_obj, AIMessage):
            final_obj = AIMessage(
                content=content_text,
                tool_calls=list(getattr(message_obj, "tool_calls", None) or [])
            )
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
        self._policy_messages_by_id[message_id] = ref
        self._policy_message_order.append(message_id)
        self._policy_message_obj_to_id[id(final_obj)] = message_id
        return final_obj

    def message_id_of_obj(self, message_obj: Any) -> str:
        """Get message ID from message object.

        Args:
            message_obj: Message object

        Returns:
            Message ID or empty string
        """
        if message_obj is None:
            return ""
        mapped = self._policy_message_obj_to_id.get(id(message_obj), "")
        if mapped:
            return mapped
        content_text = self._content_to_text(getattr(message_obj, "content", ""))
        picked = self._extract_message_ids(content_text)
        return picked[0] if picked else ""

    @staticmethod
    def _extract_message_ids(text: str) -> List[str]:
        """Extract message IDs from text.

        Args:
            text: Text to search

        Returns:
            List of message IDs found
        """
        pattern = r"消息ID:\s*(Message_\d+)"
        matches = re.findall(pattern, str(text or ""))
        return [m.strip() for m in matches if m.strip()]

    def refresh_active_flags(self, messages: List[Any]) -> None:
        """Refresh active flags based on current message list.

        Args:
            messages: Current message list
        """
        active_ids = set()
        for msg in messages:
            mid = self.message_id_of_obj(msg)
            if mid:
                active_ids.add(mid)
        for message_id, ref in self._policy_messages_by_id.items():
            ref.active = message_id in active_ids

    def fold_message(self, message_id: str, reason: str = "") -> str:
        """Fold a message to save context space.

        Args:
            message_id: Message ID to fold
            reason: Reason for folding

        Returns:
            Status: "folded", "protected", "already_folded", or "unmatched"
        """
        ref = self._policy_messages_by_id.get(str(message_id or "").strip())
        if not ref:
            return "unmatched"
        if not ref.active:
            return "unmatched"
        if ref.protected:
            return "protected"
        if ref.folded:
            return "already_folded"
        try:
            ref.message_obj.content = self.inject_message_id_text(
                self.context_fold_placeholder,
                ref.message_id
            )
            if isinstance(ref.message_obj, AIMessage):
                ref.message_obj.tool_calls = []
            ref.folded = True
            return "folded"
        except Exception:
            return "unmatched"

    def active_refs(self) -> List[PolicyMessageRef]:
        """Get all active policy message references.

        Returns:
            List of active references
        """
        rows: List[PolicyMessageRef] = []
        for mid in self._policy_message_order:
            ref = self._policy_messages_by_id.get(mid)
            if not ref:
                continue
            if not ref.active:
                continue
            rows.append(ref)
        return rows

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count from text.

        Args:
            text: Input text

        Returns:
            Estimated token count
        """
        value = str(text or "")
        if not value:
            return 0
        return max(1, len(value) // 4)

    def calculate_usage(self, messages: List[Any]) -> Dict[str, int]:
        """Calculate usage statistics for message list.

        Args:
            messages: Message list

        Returns:
            Dict with message_count, total_chars, total_tokens
        """
        total_chars = 0
        total_tokens = 0
        for msg in messages:
            body = self._content_to_text(getattr(msg, "content", ""))
            total_chars += len(body)
            total_tokens += self.estimate_tokens(body)
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                tc_text = self._content_to_text(tool_calls)
                total_chars += len(tc_text)
                total_tokens += self.estimate_tokens(tc_text)
        return {
            "message_count": len(messages),
            "total_chars": total_chars,
            "total_tokens": total_tokens,
        }
