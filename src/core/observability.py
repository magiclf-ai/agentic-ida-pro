"""Observability hub for centralized event emission."""
from typing import Any, Dict, Optional

from .session_logger import AgentSessionLogger


class ObservabilityHub:
    """Centralized observability emitter."""

    def __init__(self, logger: Optional[AgentSessionLogger], debug_enabled: bool = False):
        self.logger = logger
        self.debug_enabled = bool(debug_enabled)

    def emit(self, event: str, payload: Dict[str, Any], *, level: str = "info") -> None:
        """Emit an observability event.

        Args:
            event: Event name
            payload: Event data
            level: Log level (info, debug, etc.)
        """
        if level == "debug" and (not self.debug_enabled):
            return
        if self.logger:
            try:
                self.logger.log(event, payload)
            except Exception:
                pass
