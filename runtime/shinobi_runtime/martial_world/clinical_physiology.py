"""Shared chronology helpers for autonomous clinical treatment.

Clinical treatment mutates the same exact-person body state owned by the sparse
physiology wake.  These helpers settle any existing wake to the treatment
frontier first and then replace it from the post-treatment body, preserving
recovery/poison carry without allowing an older event anchor to survive.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .physiology_frontier import next_physiology_event, settle_person_physiology_event


def prepare_patient_for_treatment(
    person_ref: str,
    person: Mapping[str, Any],
    *,
    schedule: Mapping[str, Any],
    pending_events: Sequence[Mapping[str, Any]],
    at: str | datetime,
) -> dict[str, Any]:
    event_id = f"person_physiology_due:{person_ref}"
    event = next(
        (
            row for row in reversed(pending_events)
            if isinstance(row, Mapping) and str(row.get("event_id") or "") == event_id
        ),
        None,
    )
    if not isinstance(event, Mapping):
        one_off = schedule.get("one_off", {}) if isinstance(schedule.get("one_off"), Mapping) else {}
        raw = one_off.get(event_id) if isinstance(one_off, Mapping) else None
        event = raw if isinstance(raw, Mapping) else None
    if not isinstance(event, Mapping):
        return {
            "person_after": copy.deepcopy(dict(person)),
            "recovery_carry_minutes": 0,
            "poison_clearance_carry_minutes": 0,
            "event_id": event_id,
        }
    settled = settle_person_physiology_event(person, event, at=at)
    replacement = settled.get("next_event")
    return {
        "person_after": settled["person_after"],
        "recovery_carry_minutes": max(0, int(replacement.get("recovery_carry_minutes", 0))) if isinstance(replacement, Mapping) else 0,
        "poison_clearance_carry_minutes": max(0, int(replacement.get("poison_clearance_carry_minutes", 0))) if isinstance(replacement, Mapping) else 0,
        "event_id": event_id,
    }


def rebase_treated_patient_wakes(
    rebases: Mapping[str, Mapping[str, Any]],
    *,
    schedule: Mapping[str, Any],
    pending_events: Sequence[Mapping[str, Any]],
    at: str | datetime,
    load_person: Callable[[str], tuple[Any, ...] | Mapping[str, Any]],
) -> dict[str, Any]:
    """Replace stale pre-treatment physiology wakes with post-treatment wakes."""
    if not rebases:
        return {
            "schedule_after": schedule,
            "pending_events_after": list(pending_events),
            "rebased_event_ids": [],
        }
    replacements: dict[str, dict[str, Any] | None] = {}
    for ref, carry in rebases.items():
        if not isinstance(ref, str) or not isinstance(carry, Mapping):
            continue
        event_id = str(carry.get("event_id") or f"person_physiology_due:{ref}")
        try:
            loaded = load_person(ref)
        except (KeyError, ValueError, FileNotFoundError):
            replacements[event_id] = None
            continue
        person = loaded[-1] if isinstance(loaded, tuple) else loaded
        if not isinstance(person, Mapping):
            replacements[event_id] = None
            continue
        replacements[event_id] = next_physiology_event(
            ref,
            person,
            now=at,
            recovery_carry_minutes=max(0, int(carry.get("recovery_carry_minutes", 0))),
            poison_clearance_carry_minutes=max(0, int(carry.get("poison_clearance_carry_minutes", 0))),
        )
    rebased_ids = set(replacements)
    pending_after = [
        dict(row) for row in pending_events
        if isinstance(row, Mapping) and str(row.get("event_id") or "") not in rebased_ids
    ]
    pending_after.extend(dict(row) for row in replacements.values() if isinstance(row, Mapping))
    schedule_after = copy.deepcopy(dict(schedule))
    one_off = schedule_after.setdefault("one_off", {})
    if isinstance(one_off, dict):
        for event_id in rebased_ids:
            one_off.pop(event_id, None)
    return {
        "schedule_after": schedule_after,
        "pending_events_after": pending_after,
        "rebased_event_ids": sorted(rebased_ids),
    }


__all__ = ["prepare_patient_for_treatment", "rebase_treated_patient_wakes"]
