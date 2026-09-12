"""Executable authority for deterministic faction-autonomy rule data."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_PATH = _ROOT / "game" / "data" / "martial-world" / "autonomy.json"


@lru_cache(maxsize=1)
def _loaded() -> dict[str, Any]:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("jianghu autonomy rules invalid")
    mechanics = data.get("mechanics")
    if not isinstance(mechanics, Mapping):
        raise ValueError("jianghu autonomy mechanics missing")
    return copy.deepcopy(dict(data))


def autonomy_mechanics() -> dict[str, Any]:
    return copy.deepcopy(dict(_loaded()["mechanics"]))


def hostile_affordance(faction_type: str, stage: str) -> str:
    mechanics = _loaded()["mechanics"]
    table = mechanics.get("hostility_affordances", {}) if isinstance(mechanics, Mapping) else {}
    profile = table.get(faction_type) if isinstance(table, Mapping) else None
    if not isinstance(profile, Mapping):
        profile = table.get("_default", {}) if isinstance(table, Mapping) else {}
    value = profile.get(stage) if isinstance(profile, Mapping) else None
    return str(value) if isinstance(value, str) else ""


def outlaw_intent(outlaw_subtype: str, *, stage: str) -> str:
    mechanics = _loaded()["mechanics"]
    table = mechanics.get("outlaw_operation_intents", {}) if isinstance(mechanics, Mapping) else {}
    value = table.get(outlaw_subtype) if isinstance(table, Mapping) else None
    if not isinstance(value, str) or not value:
        value = "robbery"
    if stage == "war":
        return "revenge_strike"
    return value


__all__ = ["autonomy_mechanics", "hostile_affordance", "outlaw_intent"]
