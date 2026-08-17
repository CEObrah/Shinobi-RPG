"""Exact recurrence calculations for temporal events."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from .events import CampaignTime


class RecurrenceError(ValueError):
    pass


def _clock(recurrence: Mapping[str, Any]) -> Tuple[int, int, int]:
    raw = recurrence.get("clock", "00:00:00")
    if not isinstance(raw, str):
        raise RecurrenceError("recurrence clock must be text")
    parts = raw.split(":")
    if len(parts) != 3:
        raise RecurrenceError("recurrence clock must be HH:MM:SS")
    try:
        hour, minute, second = (int(part) for part in parts)
        # CampaignTime performs exact range validation.
        CampaignTime(1, 1, 1, hour, minute, second)
    except ValueError as exc:
        raise RecurrenceError("invalid recurrence clock") from exc
    return hour, minute, second


def next_due(
    current_due: CampaignTime,
    recurrence: Mapping[str, Any],
) -> Optional[CampaignTime]:
    """Return the exact next boundary, or ``None`` for terminal/triggered kinds."""

    if not isinstance(recurrence, Mapping):
        raise RecurrenceError("recurrence must be an object")
    kind = recurrence.get("kind")
    if kind == "fixed_interval":
        seconds = recurrence.get("interval_seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            raise RecurrenceError("fixed interval requires positive interval_seconds")
        return current_due.add_seconds(seconds)
    if kind == "calendar_month_start":
        return current_due.next_month_start(*_clock(recurrence))
    if kind == "calendar_month_end":
        return current_due.next_month_end(*_clock(recurrence))
    if kind in ("calendar_quarter_start", "calendar_quarter_end"):
        hour, minute, second = _clock(recurrence)
        quarter = (current_due.month - 1) // 3
        next_month = quarter * 3 + 4
        year = current_due.year
        if next_month > 12:
            next_month -= 12
            year += 1
        start = CampaignTime(year, next_month, 1, hour, minute, second)
        if kind == "calendar_quarter_start":
            return start
        # The end of the quarter is the final second/date of its third month at
        # the registered clock, preserving the registered policy date semantics.
        second_month = start.next_month_start(hour, minute, second)
        return second_month.next_month_end(hour, minute, second)
    if kind == "calendar_year_start":
        hour, minute, second = _clock(recurrence)
        return CampaignTime(current_due.year + 1, 1, 1, hour, minute, second)
    if kind in ("one_shot", "triggered"):
        return None
    raise RecurrenceError(f"unsupported recurrence kind: {kind!r}")


def boundaries_through(
    first_due: CampaignTime,
    recurrence: Mapping[str, Any],
    target: CampaignTime,
    *,
    limit: int = 100_000,
) -> Tuple[CampaignTime, ...]:
    if limit <= 0:
        raise ValueError("boundary limit must be positive")
    if first_due > target:
        return ()
    result = []
    current: Optional[CampaignTime] = first_due
    while current is not None and current <= target:
        if len(result) >= limit:
            raise RecurrenceError("recurrence boundary limit exceeded")
        result.append(current)
        successor = next_due(current, recurrence)
        if successor is not None and successor <= current:
            raise RecurrenceError("recurrence did not advance")
        current = successor
    return tuple(result)
