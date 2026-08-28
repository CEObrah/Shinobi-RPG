"""Deterministic event-seeking boundaries for Jianghu time advancement.

Autonomous work may generate many internal reviews before anything should stop a
standing wait.  This module owns the small policy that decides which already-
classified handoff is the first lawful player-facing boundary.  It never creates
knowledge or events; it only consumes handoff classifications produced by the
runtime.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def _classification(row: Mapping[str, Any]) -> Mapping[str, Any]:
    handoff = row.get("handoff")
    return handoff if isinstance(handoff, Mapping) else {}


def interrupts_event_seeking(row: Mapping[str, Any]) -> bool:
    """Return whether one existing handoff lawfully stops a standing wait."""
    info = _classification(row)
    return bool(info.get("interrupts_event_seeking"))


def first_event_seeking_boundary(
    handoffs: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return the first interrupting handoff in authoritative input order.

    Input order is already deterministic because frontier settlement sorts source
    events before creating handoffs.  We intentionally do not re-sort here: a
    caller may have several consequences at the same frontier and the settlement
    order is itself the causal ordering.
    """
    for row in handoffs:
        if interrupts_event_seeking(row):
            return dict(row)
    return None


def event_seeking_boundary_summary(
    handoffs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Produce the compact stop contract consumed by campaign-time reducers."""
    boundary = first_event_seeking_boundary(handoffs)
    if boundary is None:
        return {
            "interrupted": False,
            "requires_player_decision": False,
            "class": "internal",
            "event": None,
        }
    info = _classification(boundary)
    return {
        "interrupted": True,
        "requires_player_decision": bool(info.get("requires_player_decision")),
        "class": str(info.get("class") or "soft_player_facing"),
        "event": boundary,
    }


__all__ = [
    "interrupts_event_seeking",
    "first_event_seeking_boundary",
    "event_seeking_boundary_summary",
]
