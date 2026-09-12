"""Compact deterministic Jianghu causal frontier.

Recurring work stores only the next due boundary for each schedule class.  The
runtime expands the owners at that one boundary, settles them, then advances
that class to its next due time.  We never pre-write a year of daily/monthly
rows merely because they are predictable.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .events import calendar_events_between

_RECURRING_CLASSES: dict[str, dict[str, Any]] = {
    "faction_monthly": {
        "interval_days": 30,
        "event_kinds": ("faction_review", "faction_upkeep", "faction_member_cycle", "equipment_maintenance_review"),
    },
    "region_monthly": {
        "interval_days": 30,
        "event_kinds": ("regional_market_cycle", "trade_demand_review"),
    },
    "faction_annual": {
        "interval_days": 365,
        "event_kinds": ("annual_faction_life_review",),
    },
    "route_daily": {
        "interval_days": 1,
        "event_kinds": ("route_activity_cycle",),
    },
}
from .route_activity import ROUTE_SERVICE_STATUSES


# Bounded work target, never a world-size limit.  A recurring frontier with more
# owners is settled through deterministic same-timestamp continuation chunks.
# Every omitted owner remains addressable by ``owner_cursor`` and is processed
# before the schedule class advances to its next date.
_MAX_OWNERS_PER_FRONTIER_CHUNK = 64
_CLASS_OWNER_CHUNK_SIZE = {"faction_monthly": 4, "faction_annual": 4, "route_daily": 12}
_CLASS_ORDER = {
    "region_monthly": 10,
    "faction_monthly": 20,
    "faction_annual": 25,
    "route_daily": 30,
}

def _iso(value: datetime) -> str:
    return value.isoformat()


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("timestamp invalid")
    return datetime.fromisoformat(value)


def _campaign_frontier_time(value: datetime) -> datetime:
    """Map scheduler precision onto the campaign clock without skipping work.

    Exact combat can create physiology deadlines at millisecond precision while
    ``CampaignTime`` is intentionally second-granular.  A causal obligation
    between two representable campaign seconds belongs to the *next* second.
    Rounding down would let the scheduler settle future work ahead of campaign
    time; comparing the raw sub-second value on the next pass would then make
    that obligation unreachable.
    """
    if value.microsecond <= 0:
        return value.replace(microsecond=0)
    return (value + timedelta(seconds=1)).replace(microsecond=0)


def initial_schedule(
    *,
    start: datetime,
    faction_ids: Sequence[str],
    region_ids: Sequence[str],
    route_ids: Sequence[str],
) -> dict[str, Any]:
    """Build a compact next-wake schedule, independent of any outer horizon."""
    owners = {
        "faction_monthly": sorted(set(str(x) for x in faction_ids)),
        "region_monthly": sorted(set(str(x) for x in region_ids)),
        "faction_annual": sorted(set(str(x) for x in faction_ids)),
        # Routes are demand-driven owners. Merely existing on the map is not a
        # reason to wake a route every day. Active movement creation calls
        # ``sync_route_activity`` to register only routes with live operations.
        "route_daily": [],
    }
    recurring: dict[str, Any] = {}
    for class_id, spec in _RECURRING_CLASSES.items():
        if not owners.get(class_id):
            continue
        recurring[class_id] = {
            "interval_days": int(spec["interval_days"]),
            "next_due_at": _iso(start + timedelta(days=int(spec["interval_days"]))),
            "owner_cursor": 0,
            "owner_refs": owners[class_id],
            "event_kinds": list(spec["event_kinds"]),
        }
    one_off: dict[str, Any] = {}
    return {
        "schema": "jianghu-scheduler-1.0",
        "settled_through": _iso(start),
        "recurring": recurring,
        "one_off": one_off,
    }



def sync_faction_activity(
    schedule: Mapping[str, Any], *, faction_ids: Sequence[str], now: datetime,
) -> dict[str, Any]:
    """Synchronize faction recurring owners to the current faction registry.

    The mutable faction registry, not the authored launch seed, owns current
    existence.  Call this atomically when a faction is founded, destroyed,
    merged, or split.  A partially processed same-time class is left intact so
    its owner cursor cannot be re-ordered underneath deterministic settlement.
    """
    state = deepcopy(dict(schedule))
    recurring = state.setdefault("recurring", {})
    if not isinstance(recurring, dict):
        raise ValueError("scheduler recurring invalid")
    active = sorted(set(str(x) for x in faction_ids if isinstance(x, str) and x))
    for class_id in ("faction_monthly", "faction_annual"):
        spec = _RECURRING_CLASSES[class_id]
        row = recurring.get(class_id)
        if isinstance(row, Mapping) and int(row.get("owner_cursor", 0)) > 0:
            continue
        if not active:
            recurring.pop(class_id, None)
            continue
        if not isinstance(row, dict):
            recurring[class_id] = {
                "interval_days": int(spec["interval_days"]),
                "next_due_at": _iso(now + timedelta(days=int(spec["interval_days"]))),
                "owner_cursor": 0,
                "owner_refs": active,
                "event_kinds": list(spec["event_kinds"]),
            }
            continue
        row["owner_refs"] = active
    return state

def sync_route_activity(
    schedule: Mapping[str, Any], *, active_route_ids: Sequence[str], now: datetime,
) -> dict[str, Any]:
    """Synchronize the daily route class to current active movement routes.

    The route class is absent when no physical route operation is live. This
    avoids O(all_routes * days) work while preserving exact daily progression
    for every route that currently matters. A partially processed same-time
    chunk is left untouched until its cursor completes so owner ordering cannot
    change underneath resumable scheduler work.
    """
    state = deepcopy(dict(schedule))
    recurring = state.setdefault("recurring", {})
    if not isinstance(recurring, dict):
        raise ValueError("scheduler recurring invalid")
    active = sorted(set(str(x) for x in active_route_ids if isinstance(x, str) and x))
    row = recurring.get("route_daily")
    if isinstance(row, Mapping) and int(row.get("owner_cursor", 0)) > 0:
        return state
    if not active:
        recurring.pop("route_daily", None)
        return state
    if not isinstance(row, dict):
        recurring["route_daily"] = {
            "interval_days": 1,
            "next_due_at": _iso(now + timedelta(days=1)),
            "owner_cursor": 0,
            "owner_refs": active,
            "event_kinds": list(_RECURRING_CLASSES["route_daily"]["event_kinds"]),
        }
        return state
    row["owner_refs"] = active
    due = _dt(str(row.get("next_due_at")))
    if due < now:
        row["next_due_at"] = _iso(now + timedelta(days=1))
        row["owner_cursor"] = 0
    return state


def route_ids_needing_service(movements: Mapping[str, Any]) -> list[str]:
    """Return the exact route owners that still require domain settlement.

    The scheduler must not maintain its own idea of which movement statuses are
    live. A movement remains schedulable until the route reducer has released
    every person, cargo, provision and transport reservation it owns. In
    particular, ``party_extinguished`` is terminal for personnel but not for
    carried assets, so it still needs one final route turn.
    """
    if not isinstance(movements, Mapping):
        return []
    return sorted({
        str(row.get("route_ref"))
        for row in movements.values()
        if isinstance(row, Mapping)
        and str(row.get("status") or "active") in ROUTE_SERVICE_STATUSES
        and isinstance(row.get("route_ref"), str)
        and str(row.get("route_ref"))
    })




def prune_contract_expiry_events(
    schedule: Mapping[str, Any], active_contracts: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep expiry wakes only for contracts whose pre-departure deadline is live.

    Offer expiry applies while a funded contract is ``offered`` or ``accepted``.
    Once physical performance is ``in_progress``, the route/contract lifecycle
    owns completion and the old offer deadline is no longer a causal obligation.
    """
    out = deepcopy(dict(schedule))
    rows = out.get("one_off", {})
    if not isinstance(rows, dict):
        raise ValueError("scheduler one_off invalid")
    expiring = {
        str(ref) for ref, row in active_contracts.items()
        if isinstance(ref, str) and isinstance(row, Mapping)
        and str(row.get("status") or "") in {"offered", "accepted"}
    } if isinstance(active_contracts, Mapping) else set()
    for event_id, raw in list(rows.items()):
        if not isinstance(raw, Mapping) or str(raw.get("kind") or "") != "contract_expiry_due":
            continue
        if str(raw.get("owner_ref") or "") not in expiring:
            rows.pop(event_id, None)
    return out


def upsert_one_off_event(schedule: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """Register one exact future causal obligation without a polling class.

    One-off rows are current unresolved obligations, not execution history. They
    disappear immediately after settlement. Re-registering the same ``event_id``
    is idempotent only when the authoritative row is byte-equivalent.
    """
    state = deepcopy(dict(schedule))
    rows = state.setdefault("one_off", {})
    if not isinstance(rows, dict):
        raise ValueError("scheduler one_off invalid")
    event_id = event.get("event_id")
    due_at = event.get("due_at")
    kind = event.get("kind")
    if not isinstance(event_id, str) or not event_id or not isinstance(due_at, str) or not isinstance(kind, str) or not kind:
        raise ValueError("one-off event invalid")
    _dt(due_at)
    row = deepcopy(dict(event))
    existing = rows.get(event_id)
    if existing is not None and existing != row:
        raise ValueError("one-off event id conflict")
    rows[event_id] = row
    return state

def _calendar_boundary(after: datetime, through: datetime) -> tuple[datetime, list[dict[str, Any]]] | None:
    # Some major institutions publish obligations far ahead of the event date.
    # The Great Jianghu Tournament, for example, has a one-year public notice
    # and a preparation window derived from the longest connected route.  This
    # lookahead only expands a tiny static calendar; it does not wake world
    # actors or perform simulation work early.
    rows = calendar_events_between(after.date(), (through + timedelta(days=550)).date())
    expanded: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        event_at = datetime.fromisoformat(row["date"] + "T09:00:00")
        if after < event_at <= through:
            expanded.append((event_at, {
                "event_id": f"calendar:{row['event_id']}:{row['date']}",
                "kind": row["event_id"],
                "due_at": event_at.isoformat(),
                "host_place_id": row.get("host_place_id"),
                "requires_player_decision": False,
            }))
        advance_notice = row.get("advance_notice_on")
        if isinstance(advance_notice, str):
            notice_at = datetime.fromisoformat(advance_notice + "T09:00:00")
            if after < notice_at <= through:
                expanded.append((notice_at, {
                    "event_id": f"tournament_advance_notice:{row['event_id']}:{row['date']}",
                    "kind": "tournament_advance_notice",
                    "tournament_kind": row["event_id"],
                    "competition_date": row["date"],
                    "registration_opens_on": row.get("registration_opens_on"),
                    "registration_closes_on": row.get("registration_closes_on"),
                    "due_at": notice_at.isoformat(),
                    "host_place_id": row.get("host_place_id"),
                    "requires_player_decision": False,
                }))
        close = row.get("registration_closes_on")
        # Registration frontiers are tournament mechanics, not a generic
        # consequence of every calendar event that happens to have a signup
        # deadline (lectures/exhibitions are intentionally lightweight).
        formats = row.get("formats")
        if isinstance(close, str) and isinstance(formats, list) and "individual" in formats:
            close_at = datetime.fromisoformat(close + "T18:00:00")
            open_on = row.get("registration_opens_on")
            open_at = datetime.fromisoformat(str(open_on) + "T18:00:00") if isinstance(open_on, str) else close_at - timedelta(days=30)
            if after < open_at <= through:
                expanded.append((open_at, {
                    "event_id": f"registration_open:{row['event_id']}:{open_at.date().isoformat()}",
                    "kind": "tournament_registration_open",
                    "tournament_kind": row["event_id"],
                    "competition_date": row["date"],
                    "registration_closes_on": close,
                    "due_at": open_at.isoformat(),
                    "host_place_id": row.get("host_place_id"),
                    "requires_player_decision": False,
                }))
            if after < close_at <= through:
                expanded.append((close_at, {
                    "event_id": f"registration_close:{row['event_id']}:{close}",
                    "kind": "tournament_registration_close",
                    "tournament_kind": row["event_id"],
                    "competition_date": row["date"],
                    "due_at": close_at.isoformat(),
                    "host_place_id": row.get("host_place_id"),
                    "requires_player_decision": False,
                }))
            convergence_days = max(0, int(row.get("convergence_days_before", 0)))
            if convergence_days > 0:
                competition_day = datetime.fromisoformat(row["date"] + "T00:00:00")
                for day_index in range(1, convergence_days + 1):
                    # day_index 1 is the first official convergence day; the
                    # final day immediately precedes opening competition.
                    days_before = convergence_days - day_index + 1
                    converge_at = competition_day - timedelta(days=days_before) + timedelta(hours=17)
                    if after < converge_at <= through:
                        expanded.append((converge_at, {
                            "event_id": f"tournament_convergence:{row['event_id']}:{row['date']}:{day_index}",
                            "kind": "tournament_convergence_day",
                            "tournament_kind": row["event_id"],
                            "competition_date": row["date"],
                            "convergence_day_index": day_index,
                            "convergence_day_count": convergence_days,
                            "due_at": converge_at.isoformat(),
                            "host_place_id": row.get("host_place_id"),
                            "requires_player_decision": False,
                        }))
    if not expanded:
        return None
    earliest = min(at for at, _ in expanded)
    return earliest, [row for at, row in sorted(expanded, key=lambda x: (x[0], x[1]["event_id"])) if at == earliest]


def due_events(schedule: Mapping[str, Any], *, after: datetime, through: datetime) -> list[dict[str, Any]]:
    """Expand one bounded piece of the earliest due causal frontier.

    ``owner_cursor`` is a resumable work cursor, not a cardinality cap.  If a
    class has more owners than one transaction should touch, the remaining
    owners stay due at the *same timestamp*.  The production clock can therefore
    sit on that timestamp for several internal continuation transactions without
    forgetting or time-skipping any owner.
    """
    if through < after:
        raise ValueError("invalid horizon")
    candidates: list[tuple[datetime, int, str, Mapping[str, Any]]] = []
    recurring = schedule.get("recurring", {})
    if isinstance(recurring, Mapping):
        unknown = sorted(str(class_id) for class_id in recurring if str(class_id) not in _RECURRING_CLASSES)
        if unknown:
            raise ValueError(f"scheduler recurring class invalid:{','.join(unknown)}")
        for class_id, row in recurring.items():
            if not isinstance(row, Mapping):
                continue
            due_at = row.get("next_due_at")
            if not isinstance(due_at, str):
                continue
            when = _campaign_frontier_time(_dt(due_at))
            # Equality is intentional.  A previous transaction may have moved
            # campaign time onto this frontier while leaving a resumable owner
            # chunk (or another schedule class at the same timestamp) pending.
            if after <= when <= through:
                candidates.append((when, _CLASS_ORDER.get(str(class_id), 100), str(class_id), row))
    one_off = schedule.get("one_off", {})
    if isinstance(one_off, Mapping):
        due_rows: list[tuple[datetime, dict[str, Any]]] = []
        for event_id, raw in one_off.items():
            if not isinstance(event_id, str) or not isinstance(raw, Mapping) or not isinstance(raw.get("due_at"), str):
                continue
            when = _campaign_frontier_time(_dt(str(raw["due_at"])))
            if after <= when <= through:
                row = deepcopy(dict(raw)); row.setdefault("event_id", event_id)
                # Scheduler-facing time uses the campaign clock's representable
                # second. Domain-specific exact timestamps remain in their own
                # fields (for example ``last_settled_at`` or ``dying_since``).
                row["due_at"] = when.isoformat()
                due_rows.append((when, row))
        if due_rows:
            earliest_one_off = min(when for when, _ in due_rows)
            rows_at = [row for when, row in sorted(due_rows, key=lambda x: (x[0], str(x[1].get("event_id", "")))) if when == earliest_one_off]
            physiology = [row for row in rows_at if row.get("kind") == "person_physiology_due"]
            ordinary = [row for row in rows_at if row.get("kind") != "person_physiology_due"]
            if physiology:
                candidates.append((earliest_one_off, 5, "__physiology__", {"events": physiology}))
            if ordinary:
                candidates.append((earliest_one_off, 80, "__one_off__", {"events": ordinary}))
    cal = _calendar_boundary(after, through)
    if cal is not None:
        candidates.append((cal[0], 90, "__calendar__", {"events": cal[1]}))
    if not candidates:
        return []

    boundary = min(row[0] for row in candidates)
    at_boundary = [row for row in candidates if row[0] == boundary]
    # Settle one recurring class per transaction.  This keeps the write set
    # bounded and gives resource classes a deterministic ordering at ties.
    when, _priority, class_id, row = min(at_boundary, key=lambda x: (x[1], x[2]))
    if class_id in {"__calendar__", "__one_off__", "__physiology__"}:
        return deepcopy(list(row.get("events", [])))

    owners = row.get("owner_refs", [])
    kinds = row.get("event_kinds", [])
    if not isinstance(owners, Sequence) or isinstance(owners, (str, bytes)):
        return []
    if not isinstance(kinds, Sequence) or isinstance(kinds, (str, bytes)):
        return []
    ordered_owners = sorted(str(x) for x in owners)
    cursor = max(0, int(row.get("owner_cursor", 0)))
    if cursor > len(ordered_owners):
        raise ValueError("scheduler owner cursor invalid")
    chunk_size = max(1, int(_CLASS_OWNER_CHUNK_SIZE.get(class_id, _MAX_OWNERS_PER_FRONTIER_CHUNK)))
    chunk = ordered_owners[cursor:cursor + chunk_size]
    day = when.date().isoformat()
    out: list[dict[str, Any]] = []
    for owner_ref in chunk:
        for kind in sorted(str(x) for x in kinds):
            out.append({
                "event_id": f"{kind}:{owner_ref}:{day}",
                "kind": kind,
                "owner_ref": owner_ref,
                "due_at": when.isoformat(),
                "schedule_class": class_id,
                "requires_player_decision": False,
            })
    return sorted(out, key=lambda e: (e["due_at"], e["event_id"]))


def settle_schedule(
    schedule: Mapping[str, Any],
    *,
    through: datetime,
    processed_events: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Advance recurring schedule state through a committed work chunk.

    Production passes the exact events it settled.  Only that class/cursor is
    advanced. ``processed_events=None`` performs direct schedule normalization
    across all due classes for isolated schedule construction/tests; production
    settlement always supplies the exact processed event chunk.
    """
    state = deepcopy(dict(schedule))
    settled = _dt(str(state.get("settled_through")))
    if through < settled:
        raise ValueError("scheduler settlement before settled_through")
    recurring = state.get("recurring", {})
    if not isinstance(recurring, dict):
        raise ValueError("scheduler recurring invalid")

    if processed_events is None:
        for row in recurring.values():
            if not isinstance(row, dict):
                continue
            due = _dt(str(row["next_due_at"]))
            interval = max(1, int(row["interval_days"]))
            while due <= through:
                due += timedelta(days=interval)
            row["next_due_at"] = due.isoformat()
            row["owner_cursor"] = 0
        state["settled_through"] = through.isoformat()
        return state

    events = [dict(e) for e in processed_events if isinstance(e, Mapping)]
    schedule_classes = sorted({str(e.get("schedule_class")) for e in events if isinstance(e.get("schedule_class"), str)})
    if len(schedule_classes) > 1:
        raise ValueError("one recurring schedule class per frontier chunk")
    if schedule_classes:
        class_id = schedule_classes[0]
        row = recurring.get(class_id)
        if not isinstance(row, dict):
            raise ValueError("processed schedule class missing")
        due = _campaign_frontier_time(_dt(str(row["next_due_at"])))
        if due != through:
            raise ValueError("processed schedule class timestamp mismatch")
        owners = row.get("owner_refs", [])
        if not isinstance(owners, Sequence) or isinstance(owners, (str, bytes)):
            raise ValueError("scheduler owners invalid")
        ordered_owners = sorted(str(x) for x in owners)
        cursor = max(0, int(row.get("owner_cursor", 0)))
        processed_owners = sorted({str(e.get("owner_ref")) for e in events if isinstance(e.get("owner_ref"), str)})
        chunk_size = max(1, int(_CLASS_OWNER_CHUNK_SIZE.get(class_id, _MAX_OWNERS_PER_FRONTIER_CHUNK)))
        expected_owners = ordered_owners[cursor:cursor + chunk_size]
        if processed_owners != expected_owners:
            raise ValueError("scheduler processed owner chunk mismatch")
        cursor += len(expected_owners)
        if cursor >= len(ordered_owners):
            interval = max(1, int(row["interval_days"]))
            next_due = due
            while next_due <= through:
                next_due += timedelta(days=interval)
            row["next_due_at"] = next_due.isoformat()
            row["owner_cursor"] = 0
        else:
            # Same timestamp remains due; only the exact cursor advances.
            row["owner_cursor"] = cursor

    # One-off rows are unresolved causal obligations. Once their exact event is
    # committed they disappear instead of becoming scheduler history.
    one_off = state.get("one_off", {})
    if isinstance(one_off, dict):
        for event in events:
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id in one_off:
                one_off.pop(event_id, None)

    # Calendar-only rows carry no recurring schedule class. They still share
    # the production timestamp, but recurring pointers are untouched.
    state["settled_through"] = through.isoformat()
    return state


__all__ = [
    "due_events", "initial_schedule", "route_ids_needing_service",
    "settle_schedule", "sync_faction_activity", "sync_route_activity",
    "upsert_one_off_event",
]
