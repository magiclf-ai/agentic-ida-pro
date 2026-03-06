"""Working knowledge manager for the agent."""
from typing import List

from .models import WorkingKnowledge


class KnowledgeManager:
    """Manages working knowledge during agent execution."""

    def __init__(self):
        self._knowledge = WorkingKnowledge()

    @property
    def knowledge(self) -> WorkingKnowledge:
        """Get the current working knowledge."""
        return self._knowledge

    def reset(self) -> None:
        """Reset all knowledge to empty state."""
        self._knowledge = WorkingKnowledge()

    def to_markdown(self, max_items: int = 20) -> str:
        """Render working knowledge as markdown.

        Args:
            max_items: Maximum items to show per section

        Returns:
            Markdown formatted knowledge
        """
        lines = ["## Working Knowledge"]

        if self._knowledge.confirmed_facts:
            lines.append("\n### Confirmed Facts")
            for item in self._knowledge.confirmed_facts[:max_items]:
                lines.append(f"- {item}")

        if self._knowledge.hypotheses:
            lines.append("\n### Hypotheses")
            for item in self._knowledge.hypotheses[:max_items]:
                lines.append(f"- {item}")

        if self._knowledge.open_questions:
            lines.append("\n### Open Questions")
            for item in self._knowledge.open_questions[:max_items]:
                lines.append(f"- {item}")

        if self._knowledge.evidence:
            lines.append("\n### Evidence")
            for item in self._knowledge.evidence[:max_items]:
                lines.append(f"- {item}")

        if self._knowledge.next_actions:
            lines.append("\n### Next Actions")
            for item in self._knowledge.next_actions[:max_items]:
                lines.append(f"- {item}")

        if self._knowledge.do_not_repeat:
            lines.append("\n### Do Not Repeat")
            for item in self._knowledge.do_not_repeat[:max_items]:
                lines.append(f"- {item}")

        return "\n".join(lines)

    @staticmethod
    def _clean_lines(text: str) -> List[str]:
        """Clean and deduplicate lines from text.

        Args:
            text: Input text

        Returns:
            List of cleaned, unique lines
        """
        lines = str(text or "").strip().split("\n")
        seen = set()
        result = []
        for line in lines:
            clean = line.strip()
            if clean and clean not in seen:
                seen.add(clean)
                result.append(clean)
        return result

    def update(
        self,
        *,
        section: str,
        values: List[str],
        overwrite: bool = False,
        source: str = "runtime"
    ) -> None:
        """Update a knowledge section.

        Args:
            section: Section name (confirmed_facts, hypotheses, etc.)
            values: Values to add or set
            overwrite: If True, replace existing values; if False, append
            source: Source of the update (for logging)
        """
        section_lower = section.lower().strip()
        mapping = {
            "confirmed_facts": "confirmed_facts",
            "facts": "confirmed_facts",
            "hypotheses": "hypotheses",
            "hypothesis": "hypotheses",
            "open_questions": "open_questions",
            "questions": "open_questions",
            "do_not_repeat": "do_not_repeat",
            "avoid": "do_not_repeat",
            "next_actions": "next_actions",
            "actions": "next_actions",
            "evidence": "evidence",
        }

        target_field = mapping.get(section_lower)
        if not target_field:
            return

        cleaned = self._clean_lines("\n".join(values))
        if not cleaned:
            return

        current = getattr(self._knowledge, target_field)
        if overwrite:
            setattr(self._knowledge, target_field, cleaned)
        else:
            merged = current + cleaned
            unique = []
            seen = set()
            for item in merged:
                if item not in seen:
                    seen.add(item)
                    unique.append(item)
            setattr(self._knowledge, target_field, unique)
