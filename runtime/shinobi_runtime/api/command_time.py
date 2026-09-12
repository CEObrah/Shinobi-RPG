"""Deterministic command-envelope clock derived from campaign world time."""
from __future__ import annotations

import re

_CAMPAIGN_TIME = re.compile(r"^(?:SE-)?(?P<stamp>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:Z)?$")

def command_submitted_at(world_time: object) -> str:
    """Return canonical UTC-shaped command time without consulting wall clock.

    Jianghu campaign time is deterministic simulation time.  Command identity must
    therefore depend on that authority rather than process time, while preserving
    the RFC3339-shaped planner contract.
    """
    if not isinstance(world_time, str):
        raise ValueError("campaign world time must be text")
    match = _CAMPAIGN_TIME.fullmatch(world_time)
    if match is None:
        raise ValueError("campaign world time is not canonical")
    return match.group("stamp") + "Z"

__all__ = ["command_submitted_at"]
