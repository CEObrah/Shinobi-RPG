"""Typed campaign time and stable event ordering.

The current campaign calendar uses canonical strings such as
``SE-0061-02-06T21:15:00``.  ``CampaignTime`` keeps that calendar explicit so
runtime ordering never depends on locale, timezone, or wall-clock behavior.
"""

from __future__ import annotations

import calendar
import heapq
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


_CAMPAIGN_TIME = re.compile(
    r"^SE-(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})$"
)
_CAMPAIGN_DATE = re.compile(
    r"^SE-(?P<year>(?:-[0-9]{3,}|[0-9]{4,}))-"
    r"(?P<month>[0-9]{2})-(?P<day>[0-9]{2})$"
)

MAX_EVENT_ID_UTF8_BYTES = 128
MAX_EVENT_KIND_UTF8_BYTES = 128
MAX_EVENT_HOST_UTF8_BYTES = 128
MAX_EVENT_REF_UTF8_BYTES = 128
MAX_EVENT_VISIBILITY_UTF8_BYTES = 64
MAX_EVENT_PAYLOAD_UTF8_BYTES = 64 * 1024
MAX_EVENT_PAYLOAD_NODES = 2048
MAX_EVENT_PAYLOAD_DEPTH = 12
MAX_EVENT_PAYLOAD_CONTAINER_ITEMS = 256
MAX_JSON_KEY_UTF8_BYTES = 128
MAX_JSON_INTEGER_BITS = 256
MIN_EVENT_PRIORITY = -(2**31)
MAX_EVENT_PRIORITY = 2**31 - 1


def _is_proleptic_gregorian_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _validate_campaign_date(year: int, month: int, day: int) -> None:
    """Validate a proleptic Gregorian date, including signed years and zero."""

    for value, name in ((year, "year"), (month, "month"), (day, "day")):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"campaign date {name} must be an integer")
    if not 1 <= month <= 12:
        raise ValueError("campaign date month is outside 1..12")
    month_lengths = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    maximum = month_lengths[month - 1]
    if month == 2 and _is_proleptic_gregorian_leap_year(year):
        maximum = 29
    if not 1 <= day <= maximum:
        raise ValueError("campaign date day is invalid for its month and year")


@dataclass(frozen=True, order=True)
class CampaignDate:
    year: int
    month: int
    day: int

    def __post_init__(self) -> None:
        _validate_campaign_date(self.year, self.month, self.day)

    @classmethod
    def parse(cls, value: str) -> "CampaignDate":
        if not isinstance(value, str):
            raise TypeError("campaign date must be text")
        match = _CAMPAIGN_DATE.fullmatch(value)
        if match is None:
            raise ValueError(f"invalid canonical campaign date: {value!r}")
        parsed = cls(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
        if str(parsed) != value:
            raise ValueError(f"noncanonical campaign date: {value!r}")
        return parsed

    def __str__(self) -> str:
        year = (
            f"-{abs(self.year):03d}"
            if self.year < 0
            else f"{self.year:04d}"
        )
        return f"SE-{year}-{self.month:02d}-{self.day:02d}"


@dataclass(frozen=True, order=True)
class CampaignTime:
    """A validated, lexically stable Shinobi Era timestamp."""

    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int

    def __post_init__(self) -> None:
        if self.year <= 0:
            raise ValueError("campaign time has no year zero")
        # datetime performs the complete month/day/leap-year validation.
        datetime(
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
        )

    @classmethod
    def parse(cls, value: str) -> "CampaignTime":
        match = _CAMPAIGN_TIME.fullmatch(value)
        if match is None:
            raise ValueError(f"invalid canonical campaign time: {value!r}")
        return cls(*(int(match.group(name)) for name in (
            "year", "month", "day", "hour", "minute", "second"
        )))

    def __str__(self) -> str:
        return (
            f"SE-{self.year:04d}-{self.month:02d}-{self.day:02d}"
            f"T{self.hour:02d}:{self.minute:02d}:{self.second:02d}"
        )

    def add_seconds(self, seconds: int) -> "CampaignTime":
        if not isinstance(seconds, int):
            raise TypeError("seconds must be an integer")
        value = datetime(
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
        ) + timedelta(seconds=seconds)
        return CampaignTime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
        )

    def next_month_start(self, hour: int = 0, minute: int = 0, second: int = 0) -> "CampaignTime":
        year, month = self.year, self.month + 1
        if month == 13:
            year, month = year + 1, 1
        return CampaignTime(year, month, 1, hour, minute, second)

    def next_month_end(self, hour: int = 0, minute: int = 0, second: int = 0) -> "CampaignTime":
        start = self.next_month_start(hour, minute, second)
        day = calendar.monthrange(start.year, start.month)[1]
        return CampaignTime(start.year, start.month, day, hour, minute, second)


def _bounded_text(value: object, field_name: str, maximum_bytes: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    # UTF-8 is never shorter than its source character count.  This fast check
    # avoids encoding an already-obviously-oversized attacker-controlled value.
    if len(value) > maximum_bytes:
        raise ValueError(f"{field_name} exceeds its UTF-8 byte limit")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} is not valid UTF-8 text") from exc
    if len(encoded) > maximum_bytes:
        raise ValueError(f"{field_name} exceeds its UTF-8 byte limit")
    return value


def _bounded_json_object(
    value: object,
    *,
    label: str,
    max_utf8_bytes: int,
    max_nodes: int,
    max_depth: int,
    max_container_items: int,
) -> str:
    """Return canonical JSON after iterative structural and byte validation.

    Structural validation precedes ``json.dumps`` so malicious depth, cycles,
    or fanout cannot reach the recursive serializer.  The returned text is the
    exact bounded representation whose bytes downstream components may retain.
    """

    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    if any(
        isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
        for limit in (
            max_utf8_bytes,
            max_nodes,
            max_depth,
            max_container_items,
        )
    ):
        raise ValueError("JSON bounds must be positive integers")

    nodes = 0
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(f"{label} exceeds its maximum JSON depth")
        nodes += 1
        if nodes > max_nodes:
            raise ValueError(f"{label} exceeds its maximum JSON node count")

        if isinstance(current, Mapping):
            if len(current) > max_container_items:
                raise ValueError(f"{label} exceeds its object item limit")
            for key, child in current.items():
                _bounded_text(key, f"{label} key", MAX_JSON_KEY_UTF8_BYTES)
                nodes += 1
                if nodes > max_nodes:
                    raise ValueError(f"{label} exceeds its maximum JSON node count")
                stack.append((child, depth + 1))
        elif isinstance(current, (list, tuple)):
            if len(current) > max_container_items:
                raise ValueError(f"{label} exceeds its array item limit")
            stack.extend((child, depth + 1) for child in current)
        elif current is None or isinstance(current, (str, bool)):
            continue
        elif isinstance(current, int):
            if current.bit_length() > MAX_JSON_INTEGER_BITS:
                raise ValueError(f"{label} contains an oversized integer")
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError(f"{label} contains a non-finite number")
        else:
            raise ValueError(f"{label} contains a non-JSON value")

    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (OverflowError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{label} is not serializable JSON") from exc
    if len(canonical) > max_utf8_bytes:
        raise ValueError(f"{label} exceeds its UTF-8 byte limit")
    try:
        encoded = canonical.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if len(encoded) > max_utf8_bytes:
        raise ValueError(f"{label} exceeds its UTF-8 byte limit")
    return canonical


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return _bounded_json_object(
        value,
        label="event payload",
        max_utf8_bytes=MAX_EVENT_PAYLOAD_UTF8_BYTES,
        max_nodes=MAX_EVENT_PAYLOAD_NODES,
        max_depth=MAX_EVENT_PAYLOAD_DEPTH,
        max_container_items=MAX_EVENT_PAYLOAD_CONTAINER_ITEMS,
    )


@dataclass(frozen=True, order=True)
class ScheduledEvent:
    """An immutable typed event ordered by the runtime's canonical key."""

    due_at: CampaignTime
    priority: int
    event_id: str
    kind: str = field(compare=False)
    source_host: str = field(compare=False)
    target_host: str = field(compare=False)
    payload_json: str = field(default="{}", compare=False)
    dedupe_key: Optional[str] = field(default=None, compare=False)
    visibility: str = field(default="hidden", compare=False)
    requires_player: bool = field(default=False, compare=False)
    causation_id: Optional[str] = field(default=None, compare=False)
    correlation_id: Optional[str] = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.due_at, CampaignTime):
            raise TypeError("due_at must be CampaignTime")
        _bounded_text(self.event_id, "event_id", MAX_EVENT_ID_UTF8_BYTES)
        _bounded_text(self.kind, "kind", MAX_EVENT_KIND_UTF8_BYTES)
        _bounded_text(self.source_host, "source_host", MAX_EVENT_HOST_UTF8_BYTES)
        _bounded_text(self.target_host, "target_host", MAX_EVENT_HOST_UTF8_BYTES)
        _bounded_text(
            self.visibility,
            "visibility",
            MAX_EVENT_VISIBILITY_UTF8_BYTES,
        )
        for value, field_name in (
            (self.dedupe_key, "dedupe_key"),
            (self.causation_id, "causation_id"),
            (self.correlation_id, "correlation_id"),
        ):
            if value is not None:
                _bounded_text(value, field_name, MAX_EVENT_REF_UTF8_BYTES)
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
        ):
            raise TypeError("priority must be an integer")
        if not MIN_EVENT_PRIORITY <= self.priority <= MAX_EVENT_PRIORITY:
            raise ValueError("priority is outside the supported range")
        if not isinstance(self.requires_player, bool):
            raise TypeError("requires_player must be boolean")
        if not isinstance(self.payload_json, str):
            raise TypeError("payload_json must be text")
        if len(self.payload_json) > MAX_EVENT_PAYLOAD_UTF8_BYTES:
            raise ValueError("event payload exceeds its UTF-8 byte limit")
        try:
            payload_bytes = self.payload_json.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("event payload is not valid UTF-8 JSON") from exc
        if len(payload_bytes) > MAX_EVENT_PAYLOAD_UTF8_BYTES:
            raise ValueError("event payload exceeds its UTF-8 byte limit")
        try:
            parsed = json.loads(self.payload_json)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("event payload is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("event payload must be a JSON object")
        canonical = _canonical_payload(parsed)
        if canonical != self.payload_json:
            object.__setattr__(self, "payload_json", canonical)

    @classmethod
    def build(
        cls,
        *,
        due_at: CampaignTime,
        priority: int,
        event_id: str,
        kind: str,
        source_host: str,
        target_host: str,
        payload: Optional[Mapping[str, Any]] = None,
        dedupe_key: Optional[str] = None,
        visibility: str = "hidden",
        requires_player: bool = False,
        causation_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> "ScheduledEvent":
        return cls(
            due_at=due_at,
            priority=priority,
            event_id=event_id,
            kind=kind,
            source_host=source_host,
            target_host=target_host,
            payload_json=_canonical_payload({} if payload is None else payload),
            dedupe_key=dedupe_key,
            visibility=visibility,
            requires_player=requires_player,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    @property
    def payload(self) -> Dict[str, Any]:
        return json.loads(self.payload_json)

    @property
    def identity(self) -> Tuple[str, str]:
        return self.target_host, self.dedupe_key or self.event_id

    @property
    def fingerprint(self) -> Tuple[Any, ...]:
        return (
            str(self.due_at),
            self.priority,
            self.event_id,
            self.kind,
            self.source_host,
            self.target_host,
            self.payload_json,
            self.dedupe_key,
            self.visibility,
            self.requires_player,
            self.causation_id,
            self.correlation_id,
        )

    def to_record(self) -> Mapping[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "due_at": str(self.due_at),
            "priority": self.priority,
            "source_host": self.source_host,
            "target_host": self.target_host,
            "payload": self.payload,
            "dedupe_key": self.dedupe_key,
            "visibility": self.visibility,
            "requires_player": self.requires_player,
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ScheduledEvent":
        if not isinstance(record, Mapping):
            raise TypeError("scheduled event record must be an object")
        allowed = {
            "event_id", "kind", "due_at", "priority", "source_host",
            "target_host", "payload", "dedupe_key", "visibility",
            "requires_player", "causation_id", "correlation_id",
        }
        unknown = set(record) - allowed
        if unknown:
            raise ValueError(f"unknown scheduled event fields: {sorted(unknown)}")
        return cls.build(
            due_at=CampaignTime.parse(record.get("due_at")),
            priority=record.get("priority"),
            event_id=record.get("event_id"),
            kind=record.get("kind"),
            source_host=record.get("source_host"),
            target_host=record.get("target_host"),
            payload=record.get("payload", {}),
            dedupe_key=record.get("dedupe_key"),
            visibility=record.get("visibility", "hidden"),
            requires_player=record.get("requires_player", False),
            causation_id=record.get("causation_id"),
            correlation_id=record.get("correlation_id"),
        )


class EventConflict(ValueError):
    """Raised when an ID or dedupe key is reused for a different event."""


class EventQueue:
    """A deterministic global frontier with exact duplicate suppression."""

    def __init__(self, events: Iterable[ScheduledEvent] = ()) -> None:
        self._heap: List[ScheduledEvent] = []
        self._by_id: Dict[str, ScheduledEvent] = {}
        self._by_dedupe: Dict[Tuple[str, str], str] = {}
        for event in events:
            self.add(event)

    def __len__(self) -> int:
        return len(self._heap)

    def add(self, event: ScheduledEvent) -> bool:
        existing = self._by_id.get(event.event_id)
        if existing is not None:
            if existing.fingerprint == event.fingerprint:
                return False
            raise EventConflict(f"event_id reused with different content: {event.event_id}")

        identity = event.identity
        prior_id = self._by_dedupe.get(identity)
        if prior_id is not None:
            prior = self._by_id[prior_id]
            if prior.fingerprint == event.fingerprint:
                return False
            raise EventConflict(
                f"dedupe key reused with different content: {identity[0]}:{identity[1]}"
            )

        self._by_id[event.event_id] = event
        self._by_dedupe[identity] = event.event_id
        heapq.heappush(self._heap, event)
        return True

    def peek(self) -> Optional[ScheduledEvent]:
        return self._heap[0] if self._heap else None

    def pop_next_due(self, target: CampaignTime) -> Optional[ScheduledEvent]:
        event = self.peek()
        if event is None or event.due_at > target:
            return None
        event = heapq.heappop(self._heap)
        self._by_id.pop(event.event_id)
        self._by_dedupe.pop(event.identity)
        return event

    def snapshot(self) -> Tuple[ScheduledEvent, ...]:
        return tuple(sorted(self._heap))

    def replace(self, events: Iterable[ScheduledEvent]) -> None:
        """Atomically replace the queue with a prevalidated event collection."""

        replacement = EventQueue(events)
        self._heap = list(replacement._heap)
        self._by_id = dict(replacement._by_id)
        self._by_dedupe = dict(replacement._by_dedupe)

    def to_records(self) -> Tuple[Mapping[str, Any], ...]:
        return tuple(event.to_record() for event in self.snapshot())
