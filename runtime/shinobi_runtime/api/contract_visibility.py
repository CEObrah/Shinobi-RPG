"""Player-safe discovery and visibility rules for Jianghu contract owners."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.removeprefix("SE-"))
    except ValueError:
        return None


def contract_is_player_visible(
    contract: Mapping[str, Any], *, player_id: str, faction_ref: str, world_time: str
) -> bool:
    """Return whether one exact contract may be named to the current player.

    Public unclaimed offers are discoverable until expiry. Once a contract has
    a beneficiary/participants, visibility is limited to the player's faction
    or the player personally. This keeps contract discovery useful without
    turning the API into an omniscient contract registry.
    """
    status = str(contract.get("status") or "")
    beneficiary = str(contract.get("beneficiary_ref") or "")
    participants = contract.get("participants", [])
    participant_refs = {
        str(ref) for ref in participants if isinstance(ref, str)
    } if isinstance(participants, list) else set()

    if player_id in participant_refs or (faction_ref and beneficiary == faction_ref):
        return True
    if status != "offered" or beneficiary:
        return False
    now = _dt(world_time)
    expires = _dt(contract.get("expires_at"))
    return now is not None and expires is not None and expires > now


def player_visible_contract_rows(
    index: Mapping[str, Any], *, player_id: str, faction_ref: str, world_time: str,
    limit: int = 32,
) -> list[dict[str, Any]]:
    """Return bounded discoverable contract summaries for play context."""
    active = index.get("active", {}) if isinstance(index, Mapping) else {}
    if not isinstance(active, Mapping):
        return []
    rows: list[dict[str, Any]] = []
    for contract_ref in sorted(str(ref) for ref in active if isinstance(ref, str)):
        contract = active.get(contract_ref)
        if not isinstance(contract, Mapping) or not contract_is_player_visible(
            contract, player_id=player_id, faction_ref=faction_ref, world_time=world_time,
        ):
            continue
        objective = contract.get("objective", {})
        objective_kind = str(objective.get("kind") or "") if isinstance(objective, Mapping) else ""
        rows.append({
            "object_ref": f"contract:{contract_ref}",
            "contract_ref": contract_ref,
            "contract_type": str(contract.get("contract_type") or ""),
            "status": str(contract.get("status") or ""),
            "issuer_ref": str(contract.get("issuer_ref") or ""),
            "beneficiary_ref": contract.get("beneficiary_ref"),
            "reward_cash": max(0, int(contract.get("reward_cash", 0))),
            "expires_at": contract.get("expires_at"),
            "objective_kind": objective_kind,
            "minimum_escort_count": max(0, int(objective.get("minimum_escort_count", 0))) if isinstance(objective, Mapping) else 0,
        })
        if len(rows) >= max(1, min(64, int(limit))):
            break
    return rows


__all__ = ["contract_is_player_visible", "player_visible_contract_rows"]
