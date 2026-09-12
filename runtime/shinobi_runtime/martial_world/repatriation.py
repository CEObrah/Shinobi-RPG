"""Shared finite released-person repatriation operation construction."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Mapping

from .escort_living_world import escort_can_resume_field_travel


def repatriation_travel_fit(person: Mapping[str, Any]) -> bool:
    """A released person may self-travel only when conscious and field-mobile."""
    return escort_can_resume_field_travel([person])


def build_repatriation_operation(
    *, person_ref: str, owner_faction_ref: str, origin_place_ref: str, home_place_ref: str,
    at: datetime, cause_ref: str, counterparty_faction_ref: str = "", departure_delay_hours: int = 2,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if not person_ref or not owner_faction_ref or not origin_place_ref or not home_place_ref:
        raise ValueError("repatriation operation identity invalid")
    token = hashlib.sha256(
        f"{person_ref}|{owner_faction_ref}|{origin_place_ref}|{home_place_ref}|{cause_ref}".encode("utf-8")
    ).hexdigest()[:20]
    op_ref = f"operation:captive_repatriation:{token}"
    departure_at = at + timedelta(hours=max(1, int(departure_delay_hours)))
    op = {
        "faction_ref": owner_faction_ref,
        "target_faction_ref": counterparty_faction_ref,
        "operation_kind": "captive_repatriation",
        "participant_refs": [person_ref],
        # Generic return travel is target -> source, so source is home and
        # target is the person's current release/rescue location.
        "source_place_ref": home_place_ref,
        "target_place_ref": origin_place_ref,
        "started_at": at.isoformat(),
        "departure_at": departure_at.isoformat(),
        "status": "return_preparing",
        "pending_travel_direction": "return",
        "repatriation_cause_ref": cause_ref,
    }
    event = {
        "event_id": f"operation_departure:return:{op_ref}",
        "kind": "faction_operation_departure",
        "due_at": departure_at.isoformat(),
        "owner_ref": op_ref,
        "direction": "return",
        "arrival_event_kind": "faction_operation_return",
        "requires_player_decision": False,
    }
    return op_ref, op, event


__all__ = ["build_repatriation_operation", "repatriation_travel_fit"]
