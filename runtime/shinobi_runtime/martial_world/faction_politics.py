"""Static Jianghu political camp and conflict-stage derivation.

Camp is authored culture, not mutable morality.  Current hostility remains the
single causal authority for rivalry/feud/war intensity; camp can only modify a
lawful actor's willingness to act on hostility that already exists.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_PATH = _ROOT / "game" / "data" / "martial-world" / "faction-politics.json"


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    return dict(raw) if isinstance(raw, Mapping) else {}


def faction_camp(faction_ref: str | None) -> str:
    if not isinstance(faction_ref, str) or not faction_ref:
        return ""
    rows = _data().get("faction_camps", {})
    value = rows.get(faction_ref) if isinstance(rows, Mapping) else None
    return str(value) if value in {"orthodox", "unorthodox", "outlaw"} else ""


def conflict_stage(edge: Mapping[str, Any] | None) -> str:
    hostility = max(0, int(edge.get("hostility", 0))) if isinstance(edge, Mapping) else 0
    cfg = _data().get("conflict_stages", {})
    war = max(1, int(cfg.get("war_hostility_min", 65))) if isinstance(cfg, Mapping) else 65
    feud = max(1, int(cfg.get("feud_hostility_min", 45))) if isinstance(cfg, Mapping) else 45
    rivalry = max(1, int(cfg.get("rivalry_hostility_min", 30))) if isinstance(cfg, Mapping) else 30
    if hostility >= war:
        return "war"
    if hostility >= feud:
        return "feud"
    if hostility >= rivalry:
        return "rivalry"
    return "peace"


def cross_camp_pressure(camp_a: str, camp_b: str) -> int:
    a, b = str(camp_a), str(camp_b)
    if not a or not b:
        return 0
    rows = _data().get("cross_camp_pressure", {})
    if not isinstance(rows, Mapping):
        return 0
    # Static data is human-authored and may use either directional spelling.
    # Camp pressure itself is symmetric, so accept both orientations instead
    # of silently dropping e.g. unorthodox/outlaw pressure after sorting.
    direct = f"{a}|{b}"
    reverse = f"{b}|{a}"
    value = rows.get(direct, rows.get(reverse, 0))
    return max(0, int(value))


def war_operation_active_front_limit() -> int:
    cfg = _data().get("conflict_stages", {})
    return max(1, int(cfg.get("war_operation_active_front_limit", 3))) if isinstance(cfg, Mapping) else 3


__all__ = ["conflict_stage", "cross_camp_pressure", "faction_camp", "war_operation_active_front_limit"]
