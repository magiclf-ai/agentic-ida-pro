"""Centralized prompt loading and rendering."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound


class PromptManager:
    """Load and render markdown prompts from src/prompts."""

    def __init__(self, prompt_root: Optional[str] = None):
        default_root = Path(__file__).resolve().parents[1] / "prompts"
        self.prompt_root = Path(prompt_root).expanduser().resolve() if prompt_root else default_root.resolve()
        if not self.prompt_root.exists() or not self.prompt_root.is_dir():
            raise FileNotFoundError(f"Prompt directory not found: {self.prompt_root}")

        self._env = Environment(
            loader=FileSystemLoader(str(self.prompt_root)),
            undefined=StrictUndefined,
            auto_reload=True,
            cache_size=0,
            keep_trailing_newline=True,
            trim_blocks=False,
            lstrip_blocks=False,
        )

    def render(self, template_name: str, context: Optional[Dict[str, Any]] = None) -> str:
        name = str(template_name or "").strip()
        if not name:
            raise ValueError("template_name is required")
        try:
            template = self._env.get_template(name)
        except TemplateNotFound as e:
            raise FileNotFoundError(f"Prompt template not found: {name}") from e

        data = dict(context or {})
        out = template.render(**data)
        text = str(out)
        if not text.strip():
            raise ValueError(f"Rendered prompt is empty: {name}")
        return text

    def validate_required(self, template_names: List[str]) -> None:
        missing: List[str] = []
        for raw_name in template_names:
            name = str(raw_name or "").strip()
            if not name:
                continue
            path = self.prompt_root / name
            if not path.exists() or (not path.is_file()):
                missing.append(name)
        if missing:
            rows = ", ".join(missing)
            raise FileNotFoundError(f"Missing required prompt templates: {rows}")

    def list_subagent_profiles(self) -> List[str]:
        folder = self.prompt_root / "subagents"
        if not folder.exists() or (not folder.is_dir()):
            return []
        names: List[str] = []
        for path in sorted(folder.glob("*.md")):
            if path.name.startswith("_"):
                continue
            names.append(path.stem)
        return names
