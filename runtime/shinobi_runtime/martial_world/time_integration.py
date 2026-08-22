"""Atomic Jianghu frontier orchestration.

The historical monolithic reducer is preserved in ``time_integration_legacy``
while domain owners are extracted incrementally. This module remains the single
public frontier entrypoint and therefore preserves one deterministic event order,
one scheduler settlement, and one atomic write set.

Transport pagination or bounded exact sub-resolution may never become fictional
population limits. Domain reducers stage after-images into this one frontier and
do not commit state independently.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .escort import settle_monthly_escort_demand
from .escort_route import reconcile_escort_route_settlement
from .time_integration_legacy import settle_martial_world_frontier as _legacy_settle
from .warfare import expand_new_strategic_mobilizations, settle_faction_operation_arrivals

_SCHEDULER = "state/martial-world/scheduler.json"
_EXTRACTED_EVENT_KINDS = frozenset({"trade_demand_review", "faction_operation_arrival"})
_EXTRACTED_PLACEHOLDER_PREFIX = "__extracted__:"


class _OverlayRead:
    def __init__(self, read_json: Callable[[str], Mapping[str, Any]], writes: Mapping[str, Any]) -> None:
        self._read_json = read_json
        self._writes = writes

    def __call__(self, path: str) -> Any:
        if path in self._writes:
            return copy.deepcopy(self._writes[path])
        return copy.deepcopy(self._read_json(path))


def _legacy_placeholder(event: Mapping[str, Any]) -> dict[str, Any]:
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


def settle_martial_world_frontier(
    *,
    read_json: Callable[[str], Mapping[str, Any]],
    schedule: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    at: datetime,
) -> dict[str, Any]:
    """Settle one exact frontier through legacy and extracted domain owners."""
    normalized = [dict(row) for row in events if isinstance(row, Mapping)]
    extracted = [row for row in normalized if str(row.get("kind") or "") in _EXTRACTED_EVENT_KINDS]
    legacy_events = [
        _legacy_placeholder(row) if str(row.get("kind") or "") in _EXTRACTED_EVENT_KINDS else row
        for row in normalized
    ]

    # Legacy still receives every owner in the scheduler chunk, so compact
    # scheduler advancement happens exactly once. Extracted domain mechanics are
    # replaced without creating a second chronology authority.
    legacy = _legacy_settle(
        read_json=read_json,
        schedule=schedule,
        events=legacy_events,
        at=at,
    )
    writes = {
        str(path): copy.deepcopy(record)
        for path, record in dict(legacy.get("writes", {})).items()
        if isinstance(path, str)
    }
    reviews = [
        copy.deepcopy(dict(row)) for row in legacy.get("reviews", [])
        if isinstance(row, Mapping) and not _is_placeholder_review(row)
    ]
    handoffs = [
        copy.deepcopy(dict(row)) for row in legacy.get("handoffs", [])
        if isinstance(row, Mapping) and not _is_placeholder_handoff(row)
    ]
    schedule_after = copy.deepcopy(dict(legacy.get("schedule_after", writes.get(_SCHEDULER, schedule))))

    # Legacy strategic autonomy may have created a small seed operation. Expand
    # that seed into the lawful physical muster before any later arrival sees it.
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

    # Route exposure/contact remains in the mature reducer while objective
    # settlement is generalized. This post-pass operates only on escort-tagged
    # movements that actually closed at this frontier.
    route_reviews = reconcile_escort_route_settlement(
        read_json=read_json,
        writes=writes,
        events=normalized,
        at=at,
    )
    reviews.extend(route_reviews)

    return {
        "writes": writes,
        "reviews": reviews,
        "handoffs": handoffs,
        "schedule_after": schedule_after,
    }


__all__ = ["settle_martial_world_frontier"]
