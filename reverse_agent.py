#!/usr/bin/env python3
"""Project root entrypoint for running reverse agent with managed ida_service."""
from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from entrypoints.reverse_agent_service import main as reverse_agent_service_main


def main() -> int:
    return int(reverse_agent_service_main())


if __name__ == "__main__":
    raise SystemExit(main())
