"""Canonical character-domain registries derived from character-system data."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_PATH = _ROOT / "game" / "data" / "martial-world" / "character-system.json"


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    payload = json.loads(_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("jianghu character system invalid")
    return dict(payload)


def martial_discipline_keys() -> tuple[str, ...]:
    rows = _data().get("martial_disciplines", {})
    if not isinstance(rows, Mapping) or not rows:
        raise ValueError("jianghu martial discipline registry invalid")
    return tuple(str(key) for key in rows if isinstance(key, str) and key)


def professional_skill_keys() -> tuple[str, ...]:
    rows = _data().get("professional_skills", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("jianghu professional skill registry invalid")
    return tuple(str(key) for key in rows if isinstance(key, str) and key)


def attribute_keys() -> tuple[str, ...]:
    rows = _data().get("base_attributes", {})
    if not isinstance(rows, Mapping) or not rows:
        raise ValueError("jianghu attribute registry invalid")
    return tuple(str(key) for key in rows if isinstance(key, str) and key)


__all__ = ["attribute_keys", "martial_discipline_keys", "professional_skill_keys"]
