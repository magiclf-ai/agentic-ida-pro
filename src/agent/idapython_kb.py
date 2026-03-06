"""Helpers for searching and reading an IDAPython knowledge base directory."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class SearchHit:
    path: str
    line: int


_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".7z",
    ".rar",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".o",
    ".a",
    ".obj",
    ".class",
    ".pyc",
    ".woff",
    ".woff2",
    ".ttf",
}


def resolve_kb_root(path_text: str) -> Optional[Path]:
    text = str(path_text or "").strip()
    if not text:
        return None
    root = Path(text).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return None
    return root


def _is_text_candidate(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in _BINARY_SUFFIXES:
        return False
    return True


def search_regex(root_dir: Path, pattern: str, max_hits: int = 120) -> List[SearchHit]:
    root = Path(root_dir).resolve()
    if not root.exists() or not root.is_dir():
        return []

    text = str(pattern or "").strip()
    if not text:
        return []
    regex = re.compile(text)

    cap = max(1, min(int(max_hits), 2000))
    hits: List[SearchHit] = []
    for path in sorted(root.rglob("*")):
        if len(hits) >= cap:
            break
        if (not path.is_file()) or (not _is_text_candidate(path)):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for idx, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                rel = path.relative_to(root).as_posix()
                hits.append(SearchHit(path=rel, line=int(idx)))
                if len(hits) >= cap:
                    break
    return hits


def read_file_with_lineno(
    root_dir: Path,
    rel_path: str,
    line: int,
    context_lines: int = 24,
) -> str:
    root = Path(root_dir).resolve()
    target_rel = str(rel_path or "").strip().replace("\\", "/")
    if not target_rel:
        raise ValueError("missing path")
    candidate = (root / target_rel).resolve()
    try:
        candidate.relative_to(root)
    except Exception as e:
        raise ValueError(f"path is outside knowledge base: {target_rel}") from e
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"file not found: {target_rel}")

    body = candidate.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not body:
        return ""
    picked = max(1, int(line))
    radius = max(1, int(context_lines) // 2)
    start = max(1, picked - radius)
    end = min(len(body), picked + radius)

    lines: List[str] = []
    for idx in range(start, end + 1):
        lines.append(f"[{idx}] {body[idx - 1]}")
    return "\n".join(lines)
