"""Canonical lightweight economy-category lookups used by runtime reducers."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_ECONOMY = _ROOT / "game" / "data" / "martial-world" / "economy.json"


@lru_cache(maxsize=1)
def raw_material_refs() -> frozenset[str]:
    payload = json.loads(_ECONOMY.read_text(encoding="utf-8"))
    rows = payload.get("materials", {})
    if not isinstance(rows, dict):
        raise ValueError("jianghu economy materials invalid")
    return frozenset(str(ref) for ref in rows if isinstance(ref, str) and ref)


__all__ = ["raw_material_refs"]
