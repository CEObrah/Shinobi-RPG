"""Read approved exact escort rosters from institutional mission dossiers.

Institutional missions own player-approved staffing intent. Physical contract
start remains the authority that revalidates those exact people against current
availability, location, faction ownership, commitments, provisions and transport.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from shinobi_runtime.martial_world.institutional_operations import OPERATIONS_PATH


def approved_contract_escort_roster(
    read_json: Callable[[str], Any],
    *,
    contract_ref: str,
    accepted_refs: Sequence[str],
    standing_party_refs: Sequence[str],
    minimum_escort_count: int,
) -> dict[str, Any] | None:
    """Return one approved exact escort roster, or ``None`` when none exists.

    An approved institutional plan is an exact player/House staffing decision.
    Once present, departure must consume it as-is rather than replacing named
    members with deterministic reinforcements. A physically dispatched muster
    remains the same approved roster while it travels to the contract origin.
    Current physical eligibility is intentionally revalidated by contract start.
    """
    try:
        owner = read_json(OPERATIONS_PATH)
    except FileNotFoundError:
        return None
    active = owner.get("active", {}) if isinstance(owner, Mapping) else {}
    if not isinstance(active, Mapping):
        return None
    matches = [
        row for row in active.values()
        if isinstance(row, Mapping)
        and str(row.get("linked_contract_ref") or "") == str(contract_ref)
        and str(row.get("phase") or "") in {"approved", "mustering"}
        and str(row.get("mission_kind") or "") == "escort"
        and str(row.get("operation_kind") or "") == "escort_contract"
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("approved_roster_ambiguous")

    row = matches[0]
    raw_participants = row.get("participant_refs")
    if not isinstance(raw_participants, list):
        raise ValueError("approved_roster_invalid")
    participants = [str(ref) for ref in raw_participants if isinstance(ref, str) and ref]
    if not participants or len(participants) != len(raw_participants) or len(set(participants)) != len(participants):
        raise ValueError("approved_roster_invalid")

    commander_ref = str(row.get("commander_ref") or "")
    if not commander_ref or commander_ref not in participants:
        raise ValueError("approved_roster_invalid")

    accepted = {str(ref) for ref in accepted_refs if isinstance(ref, str) and ref}
    if not accepted or not accepted.issubset(set(participants)):
        raise ValueError("approved_roster_missing_principal")
    if len(participants) < max(1, int(minimum_escort_count)):
        raise ValueError("approved_roster_below_minimum")

    standing = {str(ref) for ref in standing_party_refs if isinstance(ref, str) and ref}
    core = [ref for ref in participants if ref in standing]
    temporary = [ref for ref in participants if ref not in standing]
    return {
        "escort_refs": participants,
        "core_escort_refs": core,
        "temporary_mission_escort_refs": temporary,
        "commander_ref": commander_ref,
    }


__all__ = ["approved_contract_escort_roster"]
