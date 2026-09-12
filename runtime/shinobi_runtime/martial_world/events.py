"""Finite deterministic Jianghu calendar events and tournament seeding."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from .travel import shortest_route

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game/data/martial-world"


def _data() -> dict[str, Any]:
    return json.loads((_MW / "world-events.json").read_text(encoding="utf-8"))


def _event_date(year: int, month_day: str) -> date:
    month, day = (int(x) for x in month_day.split("-"))
    return date(year, month, day)


@lru_cache(maxsize=None)
def _connected_route_preparation_days(host_place_id: str, mode: str = "foot") -> int:
    """Conservative travel horizon from every connected registered place."""
    geo = json.loads((_MW / "geography.json").read_text(encoding="utf-8"))
    travel = json.loads((_MW / "travel.json").read_text(encoding="utf-8"))
    places = geo.get("places", {}) if isinstance(geo, Mapping) else {}
    longest_hours = 0.0
    for place_id in places:
        try:
            route = shortest_route(start=str(place_id), end=host_place_id, mode=mode)
        except (KeyError, ValueError):
            continue
        longest_hours = max(longest_hours, float(route.get("baseline_hours", 0.0)))
    weather = max((int(x) for x in travel.get("weather_time_milli", {}).values()), default=1000)
    ground = max((int(x) for x in travel.get("ground_time_milli", {}).values()), default=1000)
    worst_hours = longest_hours * (weather / 1000.0) * (ground / 1000.0)
    return max(1, int(math.ceil(worst_hours / 24.0)))


def tournament_preparation_days(event_id: str, *, host_place_id: str | None = None) -> int:
    """Return the formal registration/preparation horizon before close."""
    data = _data()
    spec = data.get("calendar_events", {}).get(event_id, {})
    if not isinstance(spec, Mapping):
        raise KeyError(event_id)
    fixed = max(1, int(spec.get("registration_opens_days_before_close", 30)))
    if not bool(spec.get("derive_open_from_connected_route", False)):
        return fixed
    host = str(host_place_id or data.get("host_cycles", {}).get("great_jianghu_tournament_host") or "")
    if not host:
        return fixed
    mode = str(spec.get("preparation_route_mode") or "foot")
    route_days = _connected_route_preparation_days(host, mode)
    buffer_days = max(0, int(spec.get("preparation_buffer_days", 0)))
    return max(fixed, route_days + buffer_days)


def calendar_event_occurrence(event_id: str, year: int) -> dict[str, Any] | None:
    """Return one deterministic calendar occurrence for ``event_id`` and year.

    This is the shared occurrence authority for scheduler generation, active
    event projection, and player participation. A duration is represented by
    ``date`` through ``ends_on``; no persistent event row is required merely
    because a calendar gathering is currently active.
    """
    data = _data()
    events = data.get("calendar_events", {}) if isinstance(data, Mapping) else {}
    spec = events.get(event_id) if isinstance(events, Mapping) else None
    if not isinstance(spec, Mapping):
        return None
    cadence = str(spec.get("cadence") or "")
    if cadence == "EVERY_4_YEARS" and int(year) % 4 != 0:
        return None
    if cadence not in {"YEARLY", "EVERY_4_YEARS"}:
        return None
    try:
        when = _event_date(int(year), str(spec["month_day"]))
    except (KeyError, TypeError, ValueError):
        return None
    row: dict[str, Any] = {
        "event_id": str(event_id),
        "date": when.isoformat(),
        "year": int(year),
    }
    display_name = spec.get("display_name")
    if isinstance(display_name, str) and display_name:
        row["display_name"] = display_name
    effect = spec.get("simulation_effect")
    if isinstance(effect, str) and effect:
        row["simulation_effect"] = effect
    participation = spec.get("participation")
    if isinstance(participation, Mapping):
        row["participation"] = json.loads(json.dumps(dict(participation)))
    if isinstance(spec.get("formats"), list):
        row["formats"] = [str(x) for x in spec["formats"]]
    cycle = data.get("host_cycles", {}).get(event_id)
    if isinstance(cycle, list) and cycle:
        row["host_place_id"] = cycle[int(year) % len(cycle)]
    elif event_id == "great_jianghu_tournament":
        row["host_place_id"] = data.get("host_cycles", {}).get("great_jianghu_tournament_host")
    duration_days = max(1, int(spec.get("duration_days", 1)))
    row["ends_on"] = (when + timedelta(days=duration_days - 1)).isoformat()
    if "advance_notice_days_before" in spec:
        row["advance_notice_on"] = (
            when - timedelta(days=max(1, int(spec["advance_notice_days_before"])))
        ).isoformat()
    if "registration_closes_days_before" in spec:
        closes_on = when - timedelta(days=int(spec["registration_closes_days_before"]))
        row["registration_closes_on"] = closes_on.isoformat()
        opens_before_close = tournament_preparation_days(
            event_id, host_place_id=str(row.get("host_place_id") or "") or None
        )
        row["registration_opens_on"] = (
            closes_on - timedelta(days=max(1, opens_before_close))
        ).isoformat()
    if "convergence_days_before" in spec:
        row["convergence_days_before"] = max(0, int(spec.get("convergence_days_before", 0)))
    return row


def active_calendar_occurrences(day: date) -> list[dict[str, Any]]:
    """Return calendar occurrences whose real duration contains ``day``."""
    data = _data()
    events = data.get("calendar_events", {}) if isinstance(data, Mapping) else {}
    if not isinstance(events, Mapping):
        return []
    out: list[dict[str, Any]] = []
    # Current events do not intentionally span more than one year, but checking
    # the previous year keeps this authority correct for any future winter event
    # that crosses New Year without needing a special-case calendar branch.
    for year in (day.year - 1, day.year):
        for event_id in sorted(str(x) for x in events if isinstance(x, str)):
            row = calendar_event_occurrence(event_id, year)
            if row is None:
                continue
            try:
                start = date.fromisoformat(str(row["date"]))
                end = date.fromisoformat(str(row.get("ends_on") or row["date"]))
            except (KeyError, TypeError, ValueError):
                continue
            if start <= day <= end:
                out.append(row)
    return sorted(out, key=lambda r: (str(r.get("date") or ""), str(r.get("event_id") or "")))


def calendar_events_between(start: date, end: date) -> list[dict[str, Any]]:
    if end < start:
        raise ValueError("end before start")
    data = _data()
    events = data.get("calendar_events", {}) if isinstance(data, Mapping) else {}
    if not isinstance(events, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for year in range(start.year, end.year + 1):
        for event_id in sorted(str(x) for x in events if isinstance(x, str)):
            row = calendar_event_occurrence(event_id, year)
            if row is None:
                continue
            try:
                when = date.fromisoformat(str(row["date"]))
            except (KeyError, TypeError, ValueError):
                continue
            if start <= when <= end:
                out.append(row)
    return sorted(out, key=lambda r: (r["date"], r["event_id"]))


def tournament_bracket(entrants: Sequence[Mapping[str, Any]]) -> list[tuple[str, str | None]]:
    """Seed by public qualifying score, then stable ID. No draw RNG."""
    rows: list[tuple[int, str]] = []
    for entrant in entrants:
        ref = entrant.get("person_ref")
        score = entrant.get("public_qualifying_score", 0)
        if not isinstance(ref, str):
            raise ValueError("entrant ref")
        if isinstance(score, bool) or not isinstance(score, int):
            raise ValueError("entrant score")
        rows.append((score, ref))
    rows.sort(key=lambda x: (-x[0], x[1]))
    refs = [ref for _, ref in rows]
    out: list[tuple[str, str | None]] = []
    while len(refs) > 1:
        out.append((refs.pop(0), refs.pop(-1)))
    if refs:
        out.append((refs[0], None))
    return out


__all__ = [
    "active_calendar_occurrences",
    "calendar_event_occurrence",
    "calendar_events_between",
    "tournament_bracket",
    "tournament_preparation_days",
]
