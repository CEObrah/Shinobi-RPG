"""Aggregate civilian demographic frontier without materializing anonymous people."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .world_health import civilian_annual_demography

_CIVILIANS = "state/martial-world/civilian-populations.json"


def settle_civilian_demography(
    *, read_json: Callable[[str], Mapping[str, Any]], writes: dict[str, Any],
    events: Sequence[Mapping[str, Any]], at: datetime,
) -> dict[str, Any]:
    due = [row for row in events if isinstance(row, Mapping) and row.get("kind") == "annual_civilian_demography"]
    if not due:
        return {"reviews": [], "handoffs": []}
    raw = writes.get(_CIVILIANS)
    state = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else copy.deepcopy(dict(read_json(_CIVILIANS)))
    places = state.get("places", {}) if isinstance(state, Mapping) else {}
    if not isinstance(places, dict):
        raise ValueError("jianghu civilian populations invalid")
    total_births = total_deaths = total_migration = 0
    for place_ref, row in places.items():
        if not isinstance(row, dict):
            continue
        population = max(0, int(row.get("current_population", 0)))
        result = civilian_annual_demography(str(place_ref), population, year=at.year)
        row["current_population"] = int(result["population_after"])
        total_births += int(result["births"])
        total_deaths += int(result["deaths"])
        total_migration += int(result["net_migration"])
    writes[_CIVILIANS] = state
    return {
        "reviews": [{
            "kind": "civilian_demographic_cycle", "births": total_births,
            "deaths": total_deaths, "net_migration": total_migration,
        }],
        "handoffs": [],
    }


__all__ = ["settle_civilian_demography"]
