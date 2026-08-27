"""Player-safe discovery and visibility rules for Jianghu contract owners."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from shinobi_runtime.martial_world.money import format_copper
from shinobi_runtime.martial_world.escort import hydrate_contract_escort_objective
from shinobi_runtime.martial_world.route_intelligence import journey_intelligence_brief, route_intelligence_brief


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
    limit: int | None = None, read_json: Callable[[str], Any] | None = None,
    include_route_intelligence: bool = True,
) -> list[dict[str, Any]]:
    """Return discoverable contract summaries.

    ``limit`` is optional transport pagination only. It is never interpreted as
    a fictional cap on the number of contracts that exist.
    """
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
        objective = objective if isinstance(objective, Mapping) else {}
        escort_objective = str(contract.get("contract_type") or "") == "escort" or str(objective.get("kind") or "").startswith("escort_")
        if escort_objective:
            try:
                objective = hydrate_contract_escort_objective(objective)
            except (KeyError, TypeError, ValueError):
                # Every current escort offer must resolve to a playable physical
                # journey before it can be advertised to the player.
                continue
        reward = max(0, int(contract.get("reward_cash", 0)))
        intelligence = None
        route_refs = [str(x) for x in objective.get("route_refs", []) if isinstance(x, str)] if isinstance(objective.get("route_refs"), list) else []
        route_ref = str(objective.get("route_ref") or "")
        if include_route_intelligence:
            try:
                if len(route_refs) > 1:
                    intelligence = journey_intelligence_brief(
                        route_refs, source_place_ref=str(objective.get("source_place_ref") or ""),
                        destination_place_ref=str(objective.get("destination_place_ref") or ""), read_json=read_json,
                    )
                elif route_ref:
                    intelligence = route_intelligence_brief(
                        route_ref, source_place_ref=str(objective.get("source_place_ref") or "") or None,
                        destination_place_ref=str(objective.get("destination_place_ref") or "") or None, read_json=read_json,
                    )
            except (KeyError, TypeError, ValueError):
                intelligence = None
        row = {
            "object_ref": f"contract:{contract_ref}",
            "contract_ref": contract_ref,
            "contract_type": str(contract.get("contract_type") or ""),
            "status": str(contract.get("status") or ""),
            "issuer_ref": str(contract.get("issuer_ref") or ""),
            "beneficiary_ref": contract.get("beneficiary_ref"),
            "reward_cash": reward,
            "reward_display": format_copper(reward),
            "expires_at": contract.get("expires_at"),
            "objective_kind": str(objective.get("kind") or ""),
            "escort_kind": str(objective.get("escort_kind") or ("cargo" if objective.get("kind") == "escort_shipment" else "")),
            "route_ref": objective.get("route_ref"),
            "route_refs": list(objective.get("route_refs", [])) if isinstance(objective.get("route_refs"), list) else [],
            "places_crossed": list(objective.get("places_crossed", [])) if isinstance(objective.get("places_crossed"), list) else [],
            "distance_km_tenths": max(0, int(objective.get("distance_km_tenths", 0))),
            "expected_travel_hours": max(0, int(objective.get("expected_travel_hours", 0))),
            "terrain": objective.get("terrain"),
            "road_quality": objective.get("road_quality"),
            "item_ref": objective.get("item_ref"),
            "quantity": max(0, int(objective.get("quantity", 0))),
            "cargo_mass_kg": max(0, int(objective.get("cargo_mass_kg", 0))),
            "cargo_value_cash": max(0, int(objective.get("cargo_value_cash", 0))),
            "transport_mode": objective.get("transport_mode"),
            "freight_capacity_kg": max(0, int(objective.get("freight_capacity_kg", 0))),
            "civilian_crew_count": max(0, int(objective.get("civilian_crew_count", 0))),
            "protected_person_refs": [str(x) for x in objective.get("protected_person_refs", []) if isinstance(x, str)] if isinstance(objective.get("protected_person_refs"), list) else [],
            "protected_people_count": max(0, int(objective.get("protected_people_count", 0))),
            "minimum_escort_count": max(0, int(objective.get("minimum_escort_count", 0))),
            "threat_score": max(0, int(objective.get("threat_score", 0))),
            "route_intelligence": intelligence,
        }
        route_ref = row.get("route_ref")
        if isinstance(intelligence, Mapping):
            row["route_intelligence"] = {
                "route_ref": intelligence.get("route_ref"),
                "route_refs": list(intelligence.get("route_refs", [])) if isinstance(intelligence.get("route_refs"), list) else ([route_ref] if isinstance(route_ref, str) and route_ref else []),
                "source_place_ref": intelligence.get("source_place_ref"),
                "destination_place_ref": intelligence.get("destination_place_ref"),
                "places_crossed": list(intelligence.get("places_crossed", [])) if isinstance(intelligence.get("places_crossed"), list) else [],
                "known_route_threats": list(intelligence.get("known_route_threats", [])),
                "settlement_presence_count": len(intelligence.get("settlement_presence", [])) if isinstance(intelligence.get("settlement_presence"), list) else 0,
                "information_rule": intelligence.get("information_rule"),
            }
        rows.append(row)
        if limit is not None and len(rows) >= max(1, int(limit)):
            break
    return rows


def compact_contract_discovery_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep visible offers discoverable without repeating full terms every turn.

    Exact terms and route intelligence remain demand-loaded through
    ``inspect_game_object(contract:...)``.
    """
    fields = (
        "object_ref", "contract_ref", "contract_type", "status", "issuer_ref",
        "beneficiary_ref", "reward_display", "expires_at", "objective_kind",
        "route_refs", "places_crossed", "item_ref", "quantity",
        "protected_people_count", "minimum_escort_count", "threat_score",
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        compact: dict[str, Any] = {}
        for key in fields:
            value = row.get(key)
            if value in (None, "", [], {}):
                continue
            compact[key] = value
        out.append(compact)
    return out


__all__ = ["compact_contract_discovery_rows", "contract_is_player_visible", "player_visible_contract_rows"]
