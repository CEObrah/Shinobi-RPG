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
from .scheduler import settle_schedule
from .time_integration_legacy import settle_martial_world_frontier as _legacy_settle

_SCHEDULER = "state/martial-world/scheduler.json"
_EXTRACTED_EVENT_KINDS = frozenset({"trade_demand_review"})


class _OverlayRead:
    def __init__(self, read_json: Callable[[str], Mapping[str, Any]], writes: Mapping[str, Any]) -> None:
        self._read_json = read_json
        self._writes = writes

    def __call__(self, path: str) -> Any:
        if path in self._writes:
            return copy.deepcopy(self._writes[path])
        return copy.deepcopy(self._read_json(path))


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
    legacy_events = [row for row in normalized if str(row.get("kind") or "") not in _EXTRACTED_EVENT_KINDS]

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
    reviews = [copy.deepcopy(dict(row)) for row in legacy.get("reviews", []) if isinstance(row, Mapping)]
    handoffs = [copy.deepcopy(dict(row)) for row in legacy.get("handoffs", []) if isinstance(row, Mapping)]
    schedule_after = copy.deepcopy(dict(legacy.get("schedule_after", writes.get(_SCHEDULER, schedule))))

    if extracted:
        # Legacy did not see this recurring owner chunk, so advance precisely
        # those scheduler owners now. through==settled_through is lawful.
        schedule_after = settle_schedule(schedule_after, through=at, processed_events=extracted)
        writes[_SCHEDULER] = schedule_after
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

    return {
        "writes": writes,
        "reviews": reviews,
        "handoffs": handoffs,
        "schedule_after": schedule_after,
    }


__all__ = ["settle_martial_world_frontier"]
