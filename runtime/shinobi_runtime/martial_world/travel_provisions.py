"""Conserved route provisions shared by every physical journey.

A route movement reserves finite ration-days exactly once at departure.  Travel
progress consumes that reservation, and faction-funded travel earns a bounded
current upkeep credit so the same person-day is not charged again by monthly
household upkeep.  This module stores only current reservation/accounting state,
never append-only consumption receipts.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence

_DAY_SECONDS = 24 * 3600


def planned_journey_seconds(plan_or_movement: Mapping[str, Any]) -> int:
    rows = plan_or_movement.get("segment_required_seconds")
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
        vals = [max(1, int(x)) for x in rows if isinstance(x, int)]
        if vals:
            return sum(vals)
    segments = plan_or_movement.get("segments")
    if isinstance(segments, Sequence) and not isinstance(segments, (str, bytes)):
        seconds = 0
        for row in segments:
            if not isinstance(row, Mapping):
                continue
            if isinstance(row.get("seconds"), int):
                seconds += max(1, int(row["seconds"]))
            else:
                seconds += max(60, int(round(float(row.get("hours", 0.0)) * 3600.0)))
        if seconds > 0:
            return seconds
    if "required_seconds" in plan_or_movement:
        return max(1, int(plan_or_movement.get("required_seconds", 1)))
    return 0


def provisioning_journey_seconds(plan_or_movement: Mapping[str, Any]) -> int:
    """Return the refundable departure provisioning horizon.

    New route plans expose a worst-registered-weather duration for the chosen
    path. Existing movements without that field retain their exact historical
    reservation horizon.
    """
    if "provisioning_seconds" in plan_or_movement:
        return max(0, int(plan_or_movement.get("provisioning_seconds", 0)))
    rows = plan_or_movement.get("segment_provisioning_seconds")
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
        vals = [max(1, int(x)) for x in rows if isinstance(x, int)]
        if vals:
            return sum(vals)
    return planned_journey_seconds(plan_or_movement)


def required_ration_days(*, participant_count: int, travel_seconds: int) -> int:
    people = max(0, int(participant_count))
    seconds = max(0, int(travel_seconds))
    if people <= 0 or seconds <= 0:
        return 0
    return people * max(1, math.ceil(seconds / _DAY_SECONDS))


def make_provision_reservation(
    *, source_kind: str, source_ref: str, participant_count: int,
    travel_seconds: int, reserved_ration_days: int | None = None,
) -> dict[str, Any]:
    if source_kind not in {"person", "faction"}:
        raise ValueError("unsupported route provision source")
    people = max(0, int(participant_count))
    seconds = max(0, int(travel_seconds))
    required = required_ration_days(participant_count=people, travel_seconds=seconds)
    reserved = required if reserved_ration_days is None else max(0, int(reserved_ration_days))
    if reserved < required:
        raise ValueError("insufficient route provision reservation")
    return {
        "source_kind": source_kind,
        "source_ref": str(source_ref),
        "participant_count": people,
        "planned_travel_seconds": seconds,
        "ration_days_reserved": reserved,
        "ration_days_consumed": 0,
        "journey_elapsed_seconds": 0,
    }


def reserve_personal_rations(
    person: Mapping[str, Any], *, person_ref: str, participant_count: int, travel_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    out = copy.deepcopy(dict(person))
    required = required_ration_days(participant_count=participant_count, travel_seconds=travel_seconds)
    before = max(0, int(out.get("travel_ration_days", 0)))
    if before < required:
        raise ValueError("insufficient personal travel rations")
    out["travel_ration_days"] = before - required
    return out, make_provision_reservation(
        source_kind="person", source_ref=person_ref, participant_count=participant_count,
        travel_seconds=travel_seconds, reserved_ration_days=required,
    )


def reserve_faction_rations(
    inventory: Mapping[str, Any], *, faction_ref: str, participant_count: int, travel_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    out = copy.deepcopy(dict(inventory))
    required = required_ration_days(participant_count=participant_count, travel_seconds=travel_seconds)
    before = max(0, int(out.get("food_ration_days", 0)))
    if before < required:
        raise ValueError("insufficient faction travel rations")
    out["food_ration_days"] = before - required
    return out, make_provision_reservation(
        source_kind="faction", source_ref=faction_ref, participant_count=participant_count,
        travel_seconds=travel_seconds, reserved_ration_days=required,
    )


def apply_route_provision_progress(
    movement: Mapping[str, Any], *, progressed_seconds: int,
) -> tuple[dict[str, Any], int]:
    """Advance current provision use and return newly consumed ration-days.

    Partial days are treated as opened/consumed ration-days.  This matches the
    departure reservation rule: any started travel day requires one full daily
    ration per traveler; unused whole ration-days remain refundable until used.
    """
    out = copy.deepcopy(dict(movement))
    reservation = out.get("provision_reservation")
    if not isinstance(reservation, Mapping):
        return out, 0
    p = copy.deepcopy(dict(reservation))
    people = max(0, int(p.get("participant_count", 0)))
    reserved = max(0, int(p.get("ration_days_reserved", 0)))
    consumed_before = max(0, min(reserved, int(p.get("ration_days_consumed", 0))))
    elapsed_before = max(0, int(p.get("journey_elapsed_seconds", 0)))
    planned = max(0, int(p.get("planned_travel_seconds", 0)))
    elapsed = min(planned, elapsed_before + max(0, int(progressed_seconds))) if planned else elapsed_before + max(0, int(progressed_seconds))
    if people <= 0 or elapsed <= 0:
        target = 0
    else:
        target = min(reserved, people * max(1, math.ceil(elapsed / _DAY_SECONDS)))
    newly = max(0, target - consumed_before)
    p["journey_elapsed_seconds"] = elapsed
    p["ration_days_consumed"] = target
    out["provision_reservation"] = p
    return out, newly


def unused_reserved_rations(movement: Mapping[str, Any]) -> int:
    reservation = movement.get("provision_reservation")
    if not isinstance(reservation, Mapping):
        return 0
    return max(0, int(reservation.get("ration_days_reserved", 0)) - int(reservation.get("ration_days_consumed", 0)))


def mark_reservation_exhausted(movement: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(movement))
    reservation = out.get("provision_reservation")
    if isinstance(reservation, Mapping):
        p = copy.deepcopy(dict(reservation))
        p["ration_days_consumed"] = max(0, int(p.get("ration_days_reserved", 0)))
        p["journey_elapsed_seconds"] = max(
            max(0, int(p.get("journey_elapsed_seconds", 0))),
            max(0, int(p.get("planned_travel_seconds", 0))),
        )
        out["provision_reservation"] = p
    return out


def add_faction_upkeep_credit(inventory: Mapping[str, Any], ration_days: int) -> dict[str, Any]:
    out = copy.deepcopy(dict(inventory))
    days = max(0, int(ration_days))
    if days:
        out["travel_food_upkeep_credit_days"] = max(0, int(out.get("travel_food_upkeep_credit_days", 0))) + days
    return out


def apply_monthly_upkeep_credit(
    inventory: Mapping[str, Any], *, gross_food_due: int,
) -> tuple[dict[str, Any], int, int]:
    """Consume current travel-food credit once and return net due + credit used."""
    out = copy.deepcopy(dict(inventory))
    due = max(0, int(gross_food_due))
    credit_before = max(0, int(out.get("travel_food_upkeep_credit_days", 0)))
    used = min(due, credit_before)
    remaining = credit_before - used
    if remaining > 0:
        out["travel_food_upkeep_credit_days"] = remaining
    else:
        out.pop("travel_food_upkeep_credit_days", None)
    return out, due - used, used


def refund_unused_to_person(person: Mapping[str, Any], movement: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    out = copy.deepcopy(dict(person))
    reservation = movement.get("provision_reservation")
    if not isinstance(reservation, Mapping) or reservation.get("source_kind") != "person":
        return out, 0
    unused = unused_reserved_rations(movement)
    if unused:
        out["travel_ration_days"] = max(0, int(out.get("travel_ration_days", 0))) + unused
    return out, unused


def refund_unused_to_faction(inventory: Mapping[str, Any], movement: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    out = copy.deepcopy(dict(inventory))
    reservation = movement.get("provision_reservation")
    if not isinstance(reservation, Mapping) or reservation.get("source_kind") != "faction":
        return out, 0
    unused = unused_reserved_rations(movement)
    if unused:
        out["food_ration_days"] = max(0, int(out.get("food_ration_days", 0))) + unused
    return out, unused


__all__ = [
    "add_faction_upkeep_credit", "apply_monthly_upkeep_credit", "apply_route_provision_progress",
    "make_provision_reservation", "mark_reservation_exhausted", "planned_journey_seconds", "provisioning_journey_seconds",
    "refund_unused_to_faction", "refund_unused_to_person", "required_ration_days",
    "reserve_faction_rations", "reserve_personal_rations", "unused_reserved_rations",
]
