#!/usr/bin/env python3
"""Emit the Phase 0--9 synthetic runtime acceptance result as JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from shinobi_runtime.acceptance import run_acceptance  # noqa: E402


def main() -> int:
    summary = run_acceptance()
    print(json.dumps(summary.to_record(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
