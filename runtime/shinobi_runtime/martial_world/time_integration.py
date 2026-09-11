"""Atomic Jianghu frontier orchestration.

Domain event families are implemented by coherent frontier modules while shared
owner-loading and exact-identity services are staged through ``frontier_bridge``.
This module remains the single public frontier entrypoint and therefore preserves
one deterministic event order, one scheduler settlement, and one atomic write set.

Transport pagination or bounded exact sub-resolution may never become fictional
population limits. Domain reducers stage after-images into this one frontier and
do not commit state independently.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .escort import settle_monthly_escort_demand
from .civilian_frontier import settle_civilian_demography
from .family_frontier import settle_due_births
from .ranking_frontier import settle_ranking_publications
from .scheduler import settle_schedule
from .frontier_bridge import settle_shared_frontier as _shared_settle
from .warfare import (
    expand_new_strategic_mobilizations,
    settle_faction_operation_arrivals,
    settle_faction_operation_departures,
    settle_faction_operation_returns,
)

_SCHEDULER = "state/martial-world/scheduler.json"
_EXTRACTED_EVENT_KINDS = frozenset({
    "trade_demand_review", "faction_operation_departure", "faction_operation_arrival",
    "retinue_assignment_review", "jianghu_ranking_publication", "annual_civilian_demography",
    "faction_operation_return", "family_birth_due",
})
_EXTRACTED_PLACEHOLDER_PREFIX = "__extracted__:"


class _OverlayRead:
    def __init__(self, read_json: Callable[[str], Mapping[str, Any]], writes: Mapping[str, Any]) -> None:
        self._read_json = read_json
        self._writes = writes

    def __call__(self, path: str) -> Any:
        if path in self._writes:
            return copy.deepcopy(self._writes[path])
        return copy.deepcopy(self._read_json(path))


def _bridge_placeholder(event: Mapping[str, Any]) -> dict[str, Any]:
    """Keep extracted recurring/one-off owners visible to scheduler settlement."""
    row = copy.deepcopy(dict(event))
    row["kind"] = _EXTRACTED_PLACEHOLDER_PREFIX + str(event.get("kind") or "")
    return row


def _is_placeholder_review(row: Mapping[str, Any]) -> bool:
    if row.get("kind") != "calendar_event":
        return False
    event = row.get("event")
    return isinstance(event, Mapping) and str(event.get("kind") or "").startswith(_EXTRACTED_PLACEHOLDER_PREFIX)


def _is_placeholder_handoff(row: Mapping[str, Any]) -> bool:
    return str(row.get("kind") or "").startswith(_EXTRACTED_PLACEHOLDER_PREFIX)


def settle_core_frontier(
    *,
    read_json: Callable[[str], Mapping[str, Any]],
    schedule: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    at: datetime,
) -> dict[str, Any]:
    """Settle one exact frontier through shared services and domain owners."""
    normalized = [dict(row) for row in events if isinstance(row, Mapping)]
    extracted = [row for row in normalized if str(row.get("kind") or "") in _EXTRACTED_EVENT_KINDS]
    bridge_events = [
        _bridge_placeholder(row) if str(row.get("kind") or "") in _EXTRACTED_EVENT_KINDS else row
        for row in normalized
    ]

    # A frontier containing only directly-owned domain events bypasses the
    # shared-service bridge. The scheduler is still settled exactly once with
    # the same processed event chunk, preserving chronology while keeping work
    # owner-bounded. Mixed chunks use the bridge for the remaining event owners.
    if normalized and len(extracted) == len(normalized):
        schedule_after = settle_schedule(schedule, through=at, processed_events=normalized)
        writes: dict[str, Any] = {_SCHEDULER: copy.deepcopy(schedule_after)}
        reviews: list[dict[str, Any]] = []
        handoffs: list[dict[str, Any]] = []
    else:
        bridge = _shared_settle(
            read_json=read_json,
            schedule=schedule,
            events=bridge_events,
            at=at,
        )
        writes = {
            str(path): copy.deepcopy(record)
            for path, record in dict(bridge.get("writes", {})).items()
            if isinstance(path, str)
        }
        reviews = [
            copy.deepcopy(dict(row)) for row in bridge.get("reviews", [])
            if isinstance(row, Mapping) and not _is_placeholder_review(row)
        ]
        handoffs = [
            copy.deepcopy(dict(row)) for row in bridge.get("handoffs", [])
            if isinstance(row, Mapping) and not _is_placeholder_handoff(row)
        ]
        schedule_after = copy.deepcopy(dict(bridge.get("schedule_after", writes.get(_SCHEDULER, schedule))))

    # Strategic autonomy may create a compact operation intent. Expand that
    # intent into the lawful physical muster before any later arrival sees it.
    mobilization_reviews = expand_new_strategic_mobilizations(
        read_json=read_json,
        writes=writes,
        at=at,
    )
    reviews.extend(mobilization_reviews)

    if extracted:
        overlay = _OverlayRead(read_json, writes)
        escort = settle_monthly_escort_demand(
            read_json=overlay,
            events=extracted,
            at=at,
            schedule_after=schedule_after,
        )
        for path, record in dict(escort.get("writes", {})).items():
            if isinstance(path, str):
                writes[path] = copy.deepcopy(record)
        reviews.extend(copy.deepcopy(dict(row)) for row in escort.get("reviews", []) if isinstance(row, Mapping))
        schedule_after = copy.deepcopy(dict(escort.get("schedule_after", writes.get(_SCHEDULER, schedule_after))))
        writes[_SCHEDULER] = schedule_after

        overlay = _OverlayRead(read_json, writes)
        departure = settle_faction_operation_departures(
            read_json=overlay,
            writes=writes,
            events=extracted,
            at=at,
            schedule_after=schedule_after,
        )
        for path, record in dict(departure.get("writes", {})).items():
            if isinstance(path, str):
                writes[path] = copy.deepcopy(record)
        reviews.extend(copy.deepcopy(dict(row)) for row in departure.get("reviews", []) if isinstance(row, Mapping))
        handoffs.extend(copy.deepcopy(dict(row)) for row in departure.get("handoffs", []) if isinstance(row, Mapping))
        schedule_after = copy.deepcopy(dict(departure.get("schedule_after", writes.get(_SCHEDULER, schedule_after))))
        writes[_SCHEDULER] = schedule_after

        overlay = _OverlayRead(read_json, writes)
        warfare = settle_faction_operation_arrivals(
            read_json=overlay,
            writes=writes,
            events=extracted,
            at=at,
            schedule_after=schedule_after,
        )
        for path, record in dict(warfare.get("writes", {})).items():
            if isinstance(path, str):
                writes[path] = copy.deepcopy(record)
        reviews.extend(copy.deepcopy(dict(row)) for row in warfare.get("reviews", []) if isinstance(row, Mapping))
        handoffs.extend(copy.deepcopy(dict(row)) for row in warfare.get("handoffs", []) if isinstance(row, Mapping))
        schedule_after = copy.deepcopy(dict(warfare.get("schedule_after", writes.get(_SCHEDULER, schedule_after))))
        writes[_SCHEDULER] = schedule_after

        operation_return = settle_faction_operation_returns(
            read_json=_OverlayRead(read_json, writes), writes=writes, events=extracted,
            at=at, schedule_after=schedule_after,
        )
        for path, record in dict(operation_return.get("writes", {})).items():
            if isinstance(path, str):
                writes[path] = copy.deepcopy(record)
        reviews.extend(copy.deepcopy(dict(row)) for row in operation_return.get("reviews", []) if isinstance(row, Mapping))
        handoffs.extend(copy.deepcopy(dict(row)) for row in operation_return.get("handoffs", []) if isinstance(row, Mapping))
        schedule_after = copy.deepcopy(dict(operation_return.get("schedule_after", writes.get(_SCHEDULER, schedule_after))))
        writes[_SCHEDULER] = schedule_after

        births = settle_due_births(
            read_json=_OverlayRead(read_json, writes), writes=writes, events=extracted, at=at,
        )
        reviews.extend(copy.deepcopy(dict(row)) for row in births.get("reviews", []) if isinstance(row, Mapping))
        handoffs.extend(copy.deepcopy(dict(row)) for row in births.get("handoffs", []) if isinstance(row, Mapping))

        ranking = settle_ranking_publications(
            read_json=_OverlayRead(read_json, writes), writes=writes, events=extracted, at=at,
        )
        reviews.extend(copy.deepcopy(dict(row)) for row in ranking.get("reviews", []) if isinstance(row, Mapping))
        handoffs.extend(copy.deepcopy(dict(row)) for row in ranking.get("handoffs", []) if isinstance(row, Mapping))

        civilian = settle_civilian_demography(
            read_json=_OverlayRead(read_json, writes), writes=writes, events=extracted, at=at,
        )
        reviews.extend(copy.deepcopy(dict(row)) for row in civilian.get("reviews", []) if isinstance(row, Mapping))
        handoffs.extend(copy.deepcopy(dict(row)) for row in civilian.get("handoffs", []) if isinstance(row, Mapping))

    return {
        "writes": writes,
        "reviews": reviews,
        "handoffs": handoffs,
        "schedule_after": schedule_after,
    }


__all__ = ["settle_core_frontier"]
