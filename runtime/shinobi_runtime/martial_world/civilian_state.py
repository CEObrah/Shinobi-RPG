"""Sparse aggregate civilian-population state."""
from __future__ import annotations

import copy
from typing import Any, Mapping


def compact_civilian_state(state: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(state))
    out.pop("rule", None)
    out.pop("population_total", None)  # deterministically derived from place pools
    places = out.get("places", {})
    if not isinstance(places, Mapping):
        raise ValueError("jianghu civilian places invalid")
    compact_places: dict[str, dict[str, int]] = {}
    for place_ref, raw in places.items():
        if not isinstance(raw, Mapping):
            raise ValueError("jianghu civilian place pool invalid")
        current = int(raw.get("current_population", 0))
        reserved = int(raw.get("reserved_for_recruitment", 0))
        cursor = int(raw.get("identity_ordinal_cursor", 0))
        if current < 0 or reserved < 0 or cursor < 0:
            raise ValueError("jianghu civilian aggregate cannot be negative")
        row: dict[str, int] = {"current_population": current}
        if reserved:
            row["reserved_for_recruitment"] = reserved
        if cursor:
            row["identity_ordinal_cursor"] = cursor
        compact_places[str(place_ref)] = row
    out["places"] = compact_places
    return out


def civilian_population_total(state: Mapping[str, Any]) -> int:
    places = state.get("places", {})
    if not isinstance(places, Mapping):
        return 0
    return sum(
        int(row.get("current_population", 0)) + int(row.get("reserved_for_recruitment", 0))
        for row in places.values()
        if isinstance(row, Mapping)
    )


__all__ = ["civilian_population_total", "compact_civilian_state"]
