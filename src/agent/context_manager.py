"""Context message manager for tracking conversation context."""
from __future__ import annotations

import time
from typing import List, Optional

from .models import ContextMessageRow


class ContextManager:
    """Manages context messages separate from policy history."""

    def __init__(self, context_window_messages: int = 120):
        self._context_messages: List[ContextMessageRow] = []
        self._context_seq: int = 0
        self.context_window_messages = context_window_messages

    def reset(self) -> None:
        """Reset all context state."""
        self._context_messages.clear()
        self._context_seq = 0

    def append(
        self,
        *,
        role: str,
        source: str,
        content: str,
        turn_id: str,
        agent_id: str,
        pinned: bool = False,
    ) -> ContextMessageRow:
        """Append a context message.

        Args:
            role: Message role
            source: Message source
            content: Message content
            turn_id: Turn ID
            agent_id: Agent ID
            pinned: Whether message is pinned

        Returns:
            The created context message row
        """
        self._context_seq += 1
        row = ContextMessageRow(
            message_id=f"Context_{self._context_seq:06d}",
            role=str(role or "user"),
            source=str(source or "unknown"),
            content=str(content or ""),
            turn_id=str(turn_id or ""),
            agent_id=str(agent_id or ""),
            created_at=time.time(),
            pinned=bool(pinned),
            pruned=False,
            folded=False,
        )
        self._context_messages.append(row)
        return row

    def get_rows(
        self,
        *,
        include_pruned: bool = False,
        include_folded: bool = True
    ) -> List[ContextMessageRow]:
        """Get context message rows.

        Args:
            include_pruned: Include pruned messages
            include_folded: Include folded messages

        Returns:
            List of context message rows
        """
        rows = (
            list(self._context_messages)
            if include_pruned
            else [row for row in self._context_messages if not row.pruned]
        )
        if include_folded:
            return rows
        return [row for row in rows if not row.folded]

    def to_markdown(
        self,
        *,
        max_messages: Optional[int] = None,
        include_pruned: bool = False,
        include_folded: bool = True,
    ) -> str:
        """Render context as markdown.

        Args:
            max_messages: Maximum messages to include
            include_pruned: Include pruned messages
            include_folded: Include folded messages

        Returns:
            Markdown formatted context
        """
        rows = self.get_rows(include_pruned=include_pruned, include_folded=include_folded)
        if not rows:
            return "(empty)"

        if max_messages is None:
            max_messages = self.context_window_messages
        tail = rows[-max(1, int(max_messages)):]

        lines: List[str] = []
        for row in tail:
            state: List[str] = ["pruned" if row.pruned else "active"]
            if row.folded:
                state.append("folded")
            if row.pinned:
                state.append("pinned")

            lines.append(f"- {row.message_id} [{row.role}] state={','.join(state)} source={row.source}")
            preview = str(row.content or "")[:180]
            lines.append(f"  {preview}")

        return "\n".join(lines)

    def prune_old_messages(self, keep_count: int) -> int:
        """Prune old messages keeping only the most recent.

        Args:
            keep_count: Number of messages to keep

        Returns:
            Number of messages pruned
        """
        if len(self._context_messages) <= keep_count:
            return 0

        pruned_count = 0
        for row in self._context_messages[:-keep_count]:
            if not row.pinned and not row.pruned:
                row.pruned = True
                pruned_count += 1

        return pruned_count

    def fold_message(self, message_id: str) -> bool:
        """Fold a context message.

        Args:
            message_id: Message ID to fold

        Returns:
            True if folded, False otherwise
        """
        for row in self._context_messages:
            if row.message_id == message_id:
                if not row.folded:
                    row.folded = True
                    return True
                return False
        return False

    def all_messages(self) -> List[ContextMessageRow]:
        """Get all context messages.

        Returns:
            List of all context messages
        """
        return list(self._context_messages)
