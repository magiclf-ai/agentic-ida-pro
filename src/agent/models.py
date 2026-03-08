"""Data models for the Reverse Expert Agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List


@dataclass
class WorkingKnowledge:
    """Working knowledge accumulated during analysis."""
    confirmed_facts: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    do_not_repeat: List[str] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)


@dataclass
class PolicyMessageRef:
    """Reference to a policy message in the conversation."""
    message_id: str
    role: str
    turn_id: str
    created_at: float
    message_obj: Any
    protected: bool = False
    folded: bool = False
    active: bool = True


@dataclass
class SubAgentState:
    """State tracking for a sub-agent."""
    agent_id: str
    parent_agent_id: str
    profile: str
    priority: str
    task_md: str
    context_md: str
    status: str = "running"
    created_at: float = 0.0
    updated_at: float = 0.0
    result_md: str = ""
    error_text: str = ""
    delivered_to_parent: bool = False
