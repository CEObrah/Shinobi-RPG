"""Derived physical-party projection over real movement/deployment owners.

A mobile party is a runtime view, never another persistent owner. Deployments and
route movements continue to own purpose-specific state; this module gives shared
availability/travel code one physical vocabulary for the people moving together.
"""
from __future__ import annotations
from typing import Any, Mapping

from .route_activity import ROUTE_SERVICE_STATUSES

_TERMINAL = {"completed", "cancelled", "returned", "failed", "resolved"}

def active_mobile_parties(read_json: Any) -> list[dict[str, Any]]:
    def read(path: str) -> Mapping[str, Any]:
        try:
            value = read_json(path)
        except FileNotFoundError:
            return {}
        return value if isinstance(value, Mapping) else {}

    out: list[dict[str, Any]] = []
    deployments = read("state/martial-world/deployments.json").get("deployments", {})
    if isinstance(deployments, Mapping):
        for ref, row in sorted(deployments.items(), key=lambda item: str(item[0])):
            if not isinstance(row, Mapping) or row.get("operation_kind") == "standing_retinue":
                continue
            if str(row.get("status") or "") in _TERMINAL:
                continue
            # A deployment owns mission purpose/reservation, but once its exact
            # people enter route-operations that movement is the sole physical
            # party projection. Do not double-count the same bodies.
            if isinstance(row.get("physical_movement_ref"), str) and str(row.get("physical_movement_ref")):
                continue
            members = row.get("participant_refs", []) if isinstance(row.get("participant_refs"), list) else []
            structure = row.get("structure") if isinstance(row.get("structure"), Mapping) else {}
            if not members and isinstance(structure.get("member_refs"), list):
                members = structure["member_refs"]
            out.append({
                "party_ref": str(ref),
                "purpose_kind": str(row.get("operation_kind") or "deployment"),
                "member_refs": [str(x) for x in members if isinstance(x, str) and x],
                "owner_ref": str(row.get("faction_ref") or ""),
                "leader_ref": str(row.get("commander_ref") or structure.get("commander_ref") or ""),
                "started_at": str(row.get("started_at") or row.get("created_at") or ""),
                "origin_place_ref": str(row.get("source_place_ref") or row.get("location_ref") or ""),
                "destination_place_ref": str(row.get("target_place_ref") or ""),
                "route_ref": str(row.get("route_ref") or ""),
                "source_owner": "deployment",
            })

    route_state = read("state/martial-world/route-operations.json")
    movements = route_state.get("movements", {}) if isinstance(route_state, Mapping) else {}
    if isinstance(movements, Mapping):
        for ref, row in sorted(movements.items(), key=lambda item: str(item[0])):
            if not isinstance(row, Mapping) or str(row.get("status") or "active") not in ROUTE_SERVICE_STATUSES:
                continue
            members = row.get("participant_refs", []) if isinstance(row.get("participant_refs"), list) else []
            out.append({
                "party_ref": str(ref),
                "purpose_kind": str(row.get("movement_kind") or ("contract_escort" if row.get("contract_ref") else "route_movement")),
                "member_refs": [str(x) for x in members if isinstance(x, str) and x],
                "owner_ref": str(row.get("beneficiary_ref") or row.get("faction_ref") or ""),
                "leader_ref": str(row.get("leader_ref") or (members[0] if members else "")),
                "started_at": str(row.get("started_at") or ""),
                "origin_place_ref": str(row.get("origin_place_ref") or ""),
                "destination_place_ref": str(row.get("destination_place_ref") or ""),
                "route_ref": str(row.get("route_ref") or ""),
                "source_owner": "route_movement",
            })
    return out


__all__ = ["active_mobile_parties"]
