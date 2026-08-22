"""Atomic Jianghu frontier orchestration.

The historical monolithic reducer is preserved in ``time_integration_legacy``
while domain owners are extracted incrementally. This module remains the single
public frontier entrypoint and therefore preserves one deterministic event order,
one scheduler settlement, and one atomic write set.

Until an event kind is explicitly delegated here, the legacy reducer remains its
implementation authority. Extraction must preserve state conservation and may
never turn transport/context limits into fictional population limits.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .time_integration_legacy import settle_martial_world_frontier as _legacy_settle


def settle_martial_world_frontier(
    *,
    read_json: Callable[[str], Mapping[str, Any]],
    schedule: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    at: datetime,
) -> dict[str, Any]:
    """Settle one exact frontier atomically.

    Domain extraction is deliberately incremental. The untouched frontier still
    executes through the frozen legacy reducer, making this seam behavior-neutral
    until a specific event class is moved behind an explicit domain reducer.
    """
    return _legacy_settle(
        read_json=read_json,
        schedule=schedule,
        events=events,
        at=at,
    )


__all__ = ["settle_martial_world_frontier"]
