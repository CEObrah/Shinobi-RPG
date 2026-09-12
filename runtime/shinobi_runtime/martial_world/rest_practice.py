"""Conserved autonomous self-practice during real rest windows.

This module never creates time. It only partitions already elapsed journey time
or measures overlap with a registered safe-lodging evening window. Practice is
self-directed and receives no faction instructor or facility bonus.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .field_development import _apply_gain, _health_milli

_ROOT = Path(__file__).resolve().parents[3]
_RULE_PATH = _ROOT / "game" / "data" / "martial-world" / "rest-practice.json"
_COMBAT_DOMAINS = ("sword", "spear", "bow", "hidden_weapons", "unarmed")
_SELF_FOCI = frozenset((*_COMBAT_DOMAINS, "stealth_scouting", "command", "qi", "qi_control"))
_ALLOWED_PRACTICE_DOMAINS = frozenset((*_SELF_FOCI, "professional:medicine"))


@lru_cache(maxsize=1)
def rest_practice_rules() -> Mapping[str, Any]:
    return json.loads(_RULE_PATH.read_text(encoding="utf-8"))


def safe_lodging_site(site: Mapping[str, Any] | None) -> bool:
    if not isinstance(site, Mapping):
        return False
    allowed = rest_practice_rules().get("safe_lodging_site_types", [])
    return str(site.get("site_type") or "") in {str(x) for x in allowed if isinstance(x, str)}


def evening_practice_hours_milli(start: datetime, end: datetime) -> int:
    """Return exact overlap with the registered evening practice window."""
    if end <= start:
        return 0
    rules = rest_practice_rules()
    start_hour = max(0, min(23, int(rules.get("evening_practice_start_hour", 19))))
    end_hour = max(1, min(24, int(rules.get("evening_practice_end_hour", 21))))
    if end_hour <= start_hour:
        return 0
    cursor = start.date()
    last = end.date()
    total_seconds = 0.0
    while cursor <= last:
        window_start = datetime.combine(cursor, time(start_hour, 0))
        if end_hour == 24:
            window_end = datetime.combine(cursor + timedelta(days=1), time(0, 0))
        else:
            window_end = datetime.combine(cursor, time(end_hour, 0))
        overlap_start = max(start, window_start)
        overlap_end = min(end, window_end)
        if overlap_end > overlap_start:
            total_seconds += (overlap_end - overlap_start).total_seconds()
        cursor += timedelta(days=1)
    return max(0, int(round(total_seconds * 1000.0 / 3600.0)))


def journey_hour_budget(elapsed_hours_milli: int) -> dict[str, int]:
    """Split elapsed journey clock into active route, rest-practice and other rest.

    Speeds in travel.json are distance per elapsed day, so journey time cannot
    also be treated as continuous marching/riding. Each started 24-hour block
    carries a bounded active-route budget; a smaller practice allowance may be
    drawn only from the remaining non-route hours on sufficiently long trips.
    """
    elapsed = max(0, int(elapsed_hours_milli))
    if elapsed <= 0:
        return {"elapsed_hours_milli": 0, "active_route_hours_milli": 0, "rest_practice_hours_milli": 0, "other_rest_hours_milli": 0}
    rules = rest_practice_rules()
    day = 24_000
    periods = max(1, (elapsed + day - 1) // day)
    active_per = max(0, min(24, int(rules.get("journey_active_route_hours_per_24", 8)))) * 1000
    practice_per = max(0, min(8, int(rules.get("journey_rest_practice_hours_per_24", 1)))) * 1000
    minimum = max(0, int(rules.get("journey_minimum_elapsed_hours_for_rest_practice", 10))) * 1000
    active = min(elapsed, periods * active_per)
    non_route = max(0, elapsed - active)
    practice = 0 if elapsed < minimum else min(non_route, periods * practice_per)
    return {
        "elapsed_hours_milli": elapsed,
        "active_route_hours_milli": active,
        "rest_practice_hours_milli": practice,
        "other_rest_hours_milli": max(0, non_route - practice),
    }


def practice_domain(person: Mapping[str, Any], *, retinue_role: str | None = None) -> str | None:
    """Choose a lawful autonomous self-practice domain without overriding policy."""
    state = person.get("training_state", {}) if isinstance(person.get("training_state"), Mapping) else {}
    focus = state.get("focus")
    if isinstance(focus, str) and focus in _SELF_FOCI:
        return focus
    role = str(retinue_role or "")
    if role == "field_medic":
        return "professional:medicine"
    if role == "scout":
        return "stealth_scouting"
    if role == "field_deputy":
        return "command"
    if role == "protective_guard":
        martial = person.get("martial_skills", {}) if isinstance(person.get("martial_skills"), Mapping) else {}
        return max(_COMBAT_DOMAINS, key=lambda key: (max(0, int(martial.get(key, 0))), key))
    return None


def practice_pressure_milli(*, journey: bool) -> int:
    rules = rest_practice_rules()
    key = "journey_rest_pressure_milli" if journey else "safe_lodging_pressure_milli"
    return max(0, min(1400, int(rules.get(key, 500 if journey else 650))))


def apply_rest_practice(
    person: Mapping[str, Any], *, duration_hours_milli: int,
    domain: str | None, pressure_milli: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply self-practice through the existing fractional training authority."""
    out = copy.deepcopy(dict(person))
    hours = max(0, int(duration_hours_milli))
    if domain not in _ALLOWED_PRACTICE_DOMAINS or hours <= 0 or _health_milli(out) < 350:
        return out, {"domain": domain, "duration_hours_milli": hours, "gain_milli": 0, "points": 0, "evidence_added_milli": 0}
    gain = _apply_gain(
        out,
        domain=str(domain),
        effective_hours_milli=hours,
        pressure_milli=max(0, min(1400, int(pressure_milli))),
        evidence_gain_milli=max(1, hours // 30),
        recovery_milli=900,
    )
    return out, {"duration_hours_milli": hours, **gain}


__all__ = [
    "apply_rest_practice",
    "evening_practice_hours_milli",
    "journey_hour_budget",
    "practice_domain",
    "practice_pressure_milli",
    "rest_practice_rules",
    "safe_lodging_site",
]
