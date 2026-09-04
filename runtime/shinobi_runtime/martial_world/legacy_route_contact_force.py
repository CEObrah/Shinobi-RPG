"""One-shot normalization for legacy active route-contact force geometry.

Fresh route contacts already size a plausible detachment before exact combat is
created. Older persistent contacts can predate that policy and may therefore
carry an impossible whole-faction attacker roster. This migration only runs at
the pre-first-exchange boundary, before the existing field-equipment migration,
and only when current durable facts prove the contact had no cargo/ransom value
input that would change the modern detachment-sizing call.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Callable, Mapping

from shinobi_runtime.combat.geometry import initial_positions, range_band_from_distance_mm

from .escort_living_world import interception_force_size
from .faction_state import faction_path, hydrate_faction_state, resolved_faction_type
from .infrastructure import enterprise_scale_value

_ROUTE_OPERATIONS_PATH = "state/martial-world/route-operations.json"
_COMBATS_PATH = "state/martial-world/combats.json"


def _ref_list(value: object, code: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(code)
    refs = [str(ref) for ref in value if isinstance(ref, str) and ref]
    if len(refs) != len(value) or len(set(refs)) != len(refs):
        raise ValueError(code)
    if not refs and not allow_empty:
        raise ValueError(code)
    return refs


def _zero_value_hostile_interception(movement: Mapping[str, Any]) -> bool:
    """Whether modern force sizing can safely use known_value_cash=0.

    With no physical cargo, a recognized positive ransom would have produced a
    ``kidnap_principal`` intent in the current route decision policy. Therefore
    a durable ``hostile_interception`` contact plus empty cargo/protected-person
    owners proves the modern force-size value input was zero.
    """
    if str(movement.get("contact_intent") or "") != "hostile_interception":
        return False
    if max(0, int(movement.get("quantity", 0) or 0), int(movement.get("cargo_quantity", 0) or 0)) > 0:
        return False
    for key in ("protected_person_refs", "captive_refs", "rescued_refs"):
        refs = movement.get(key, [])
        if refs is None:
            continue
        if not isinstance(refs, list):
            return False
        if any(isinstance(ref, str) and ref for ref in refs):
            return False
    return True


def _zero_value_objective(movement_ref: str, movement: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(movement.get("movement_kind") or "")
    if kind in {"player_strategic_travel", "faction_operation_travel", "route_pursuit"}:
        return {"kind": "preserve_route_mission", "movement_ref": movement_ref}
    if kind in {"escort_contract", "escort_return", "escort_emergency_return"} or movement.get("contract_ref"):
        return {"kind": "preserve_escort_mission", "movement_ref": movement_ref}
    return {"kind": "survive_interception", "movement_ref": movement_ref}


def _centroid(positions: Mapping[str, Any], refs: list[str]) -> tuple[int, int]:
    rows = [positions.get(ref) for ref in refs]
    if not rows or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("legacy route contact position identity mismatch")
    return (
        sum(int(row.get("x_mm", 0)) for row in rows if isinstance(row, Mapping)) // len(rows),
        sum(int(row.get("y_mm", 0)) for row in rows if isinstance(row, Mapping)) // len(rows),
    )


def _reseed_preexchange_positions(combat: dict[str, Any], attacker_side: str) -> None:
    sides = combat.get("sides")
    positions = combat.get("positions")
    if not isinstance(sides, dict) or not isinstance(positions, dict) or len(sides) != 2:
        raise ValueError("legacy route contact combat geometry invalid")
    side_refs = {str(side): _ref_list(refs, "legacy route contact combat side invalid") for side, refs in sides.items()}
    if attacker_side not in side_refs:
        raise ValueError("legacy route contact attacker side unresolved")
    other_sides = [side for side in side_refs if side != attacker_side]
    if len(other_sides) != 1:
        raise ValueError("legacy route contact opposing side unresolved")
    defender_side = other_sides[0]

    ax, ay = _centroid(positions, side_refs[attacker_side])
    dx, dy = _centroid(positions, side_refs[defender_side])
    separation = math.isqrt((ax - dx) * (ax - dx) + (ay - dy) * (ay - dy))
    band = range_band_from_distance_mm(separation)
    zone_ref = str(combat.get("zone_ref") or "")
    if not zone_ref:
        raise ValueError("legacy route contact combat zone missing")

    side_by_participant = {
        ref: side
        for side, refs in side_refs.items()
        for ref in refs
    }
    reseeded = initial_positions(
        side_by_participant=side_by_participant,
        zone_ref=zone_ref,
        initial_range_band=band,
    )
    for ref, row in reseeded.items():
        old = positions.get(ref)
        if not isinstance(old, Mapping):
            raise ValueError("legacy route contact combat position missing")
        for key in ("elevation_mm", "cover_milli", "body_radius_mm"):
            if key in old:
                row[key] = copy.deepcopy(old[key])
    combat["positions"] = reseeded


def _prune_removed_combat_refs(combat: dict[str, Any], removed: set[str]) -> None:
    combatants = combat.get("combatants")
    positions = combat.get("positions")
    if not isinstance(combatants, dict) or not isinstance(positions, dict):
        raise ValueError("legacy route contact combat participant state invalid")
    for ref in removed:
        if ref not in combatants or ref not in positions:
            raise ValueError("legacy route contact attacker state mismatch")
        combatants.pop(ref, None)
        positions.pop(ref, None)

    for state in combatants.values():
        if not isinstance(state, dict):
            raise ValueError("legacy route contact combatant state invalid")
        for key in ("observed_refs", "current_contact_refs"):
            refs = state.get(key)
            if isinstance(refs, list):
                state[key] = [ref for ref in refs if ref not in removed]
        for key in ("last_known_positions", "known_positions"):
            rows = state.get(key)
            if isinstance(rows, dict):
                for ref in removed:
                    rows.pop(ref, None)
        defense = state.get("defense_state")
        if isinstance(defense, dict):
            recent = defense.get("recent_attackers")
            if isinstance(recent, dict):
                for ref in removed:
                    recent.pop(ref, None)
        if state.get("assigned_target_ref") in removed:
            state.pop("assigned_target_ref", None)

    # No exchange has started, so cached plans/pending declarations are derived
    # scratch and must be regenerated from the resized physical formation.
    combat.pop("team_plans", None)
    combat.pop("_pending_actions", None)
    combat.pop("_defense_interruptions", None)
    combat.pop("_exchange_declared_at_ms", None)


def reconcile_legacy_active_route_contact_force_records(
    *,
    read_json: Callable[[str], Any],
    combat_ref: str,
) -> dict[str, Mapping[str, Any]]:
    """Stage one impossible legacy outlaw detachment down to current policy.

    Fresh/current contacts are authoritative and untouched. The existing field-
    equipment marker is also the migration-generation marker: once that key is
    present, this function never re-sizes the force. A pre-marker contact may be
    normalized only at ``elapsed_ms == 0`` so no already-resolved combat history
    is silently rewritten.
    """
    if not isinstance(combat_ref, str) or not combat_ref:
        raise ValueError("legacy route contact combat_ref required")

    route_ops = copy.deepcopy(read_json(_ROUTE_OPERATIONS_PATH))
    combats_state = copy.deepcopy(read_json(_COMBATS_PATH))
    if not isinstance(route_ops, dict) or not isinstance(combats_state, dict):
        raise ValueError("legacy route contact owner invalid")
    contacts = route_ops.get("contacts", {})
    movements = route_ops.get("movements", {})
    combats = combats_state.get("combats", {})
    if not isinstance(contacts, dict) or not isinstance(movements, dict) or not isinstance(combats, dict):
        raise ValueError("legacy route contact indexes invalid")

    matches = [
        (str(ref), row)
        for ref, row in contacts.items()
        if isinstance(ref, str)
        and isinstance(row, Mapping)
        and str(row.get("combat_ref") or "") == combat_ref
        and str(row.get("status") or "") == "active"
    ]
    if not matches:
        return {}
    if len(matches) != 1:
        raise ValueError("legacy route contact combat identity ambiguous")
    contact_ref, raw_contact = matches[0]
    contact = copy.deepcopy(dict(raw_contact))

    # Presence, including a historical zero, proves this contact already crossed
    # the modern one-shot migration boundary. Never re-size it afterward.
    if "field_equipment_materialized_count" in contact:
        return {}

    movement_ref = str(contact.get("movement_ref") or "")
    attacker_faction_ref = str(contact.get("attacker_faction_ref") or "")
    if not movement_ref or not attacker_faction_ref:
        raise ValueError("legacy route contact identity incomplete")
    movement = movements.get(movement_ref)
    combat = combats.get(combat_ref)
    if not isinstance(movement, Mapping) or not isinstance(combat, Mapping):
        raise ValueError("legacy route contact linked owner missing")
    movement = copy.deepcopy(dict(movement))
    combat = copy.deepcopy(dict(combat))

    if str(movement.get("contact_ref") or "") != contact_ref:
        raise ValueError("legacy route contact movement identity mismatch")
    if str(movement.get("combat_ref") or "") != combat_ref:
        raise ValueError("legacy route contact movement combat mismatch")
    if str(movement.get("contact_attacker_faction_ref") or "") != attacker_faction_ref:
        raise ValueError("legacy route contact movement faction mismatch")
    if str(combat.get("combat_id") or "") != combat_ref or str(combat.get("status") or "") != "active":
        raise ValueError("legacy route contact combat identity mismatch")
    if int(combat.get("elapsed_ms", 0) or 0) != 0:
        raise ValueError("legacy route contact force cannot reconcile after combat start")
    if not _zero_value_hostile_interception(movement):
        return {}

    attacker_refs = _ref_list(contact.get("attacker_refs"), "legacy route contact attacker roster invalid")
    movement_attackers = _ref_list(
        movement.get("contact_attacker_refs"),
        "legacy route contact movement attacker roster invalid",
    )
    if movement_attackers != attacker_refs:
        raise ValueError("legacy route contact attacker roster mismatch")
    escort_refs = _ref_list(contact.get("escort_refs"), "legacy route contact escort roster invalid")

    sides = combat.get("sides")
    if not isinstance(sides, dict):
        raise ValueError("legacy route contact combat sides invalid")
    attacker_set = set(attacker_refs)
    matching_sides = [
        str(side)
        for side, refs in sides.items()
        if isinstance(refs, list) and len(refs) == len(attacker_refs) and set(refs) == attacker_set
    ]
    if len(matching_sides) != 1:
        raise ValueError("legacy route contact attacker side mismatch")
    attacker_side = matching_sides[0]

    faction_raw = read_json(faction_path(attacker_faction_ref))
    if not isinstance(faction_raw, Mapping) or faction_raw.get("faction_id") != attacker_faction_ref:
        raise ValueError("legacy route contact attacker faction owner invalid")
    faction = hydrate_faction_state(faction_raw)
    attacker_type = resolved_faction_type(faction)
    if attacker_type != "outlaw_faction":
        return {}
    enterprises = faction.get("enterprises", {}) if isinstance(faction.get("enterprises"), Mapping) else {}
    criminal_level = max(0, int(enterprises.get("criminal_enterprise", 0)))
    criminal_scale = enterprise_scale_value(faction, "criminal_enterprise") if criminal_level > 0 else 0
    autonomy = faction.get("autonomy_policy", {}) if isinstance(faction.get("autonomy_policy"), Mapping) else {}
    risk_tolerance = max(0, int(autonomy.get("risk_tolerance", 50)))

    desired_count = interception_force_size(
        available_count=len(attacker_refs),
        observed_escort_count=len(escort_refs),
        hostility=0,
        criminal_scale=criminal_scale,
        risk_tolerance=risk_tolerance,
        known_value_cash=0,
        attacker_faction_type=attacker_type,
    )
    if desired_count <= 0 or desired_count >= len(attacker_refs):
        return {}

    kept = attacker_refs[:desired_count]
    removed = set(attacker_refs[desired_count:])
    contact["attacker_refs"] = kept
    contact["legacy_force_reconciled_from_count"] = len(attacker_refs)
    contact["legacy_force_reconciled_count"] = len(kept)
    movement["contact_attacker_refs"] = kept

    combat_sides = copy.deepcopy(dict(sides))
    combat_sides[attacker_side] = kept
    combat["sides"] = combat_sides
    _prune_removed_combat_refs(combat, removed)
    _reseed_preexchange_positions(combat, attacker_side)
    combat["objective"] = _zero_value_objective(movement_ref, movement)

    contacts[contact_ref] = contact
    movements[movement_ref] = movement
    combats[combat_ref] = combat
    route_ops["contacts"] = contacts
    route_ops["movements"] = movements
    combats_state["combats"] = combats
    return {
        _ROUTE_OPERATIONS_PATH: route_ops,
        _COMBATS_PATH: combats_state,
    }


__all__ = ["reconcile_legacy_active_route_contact_force_records"]
