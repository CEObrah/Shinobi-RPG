"""Reconcile route-contact state around exact player combat.

A route interception and its exact combat are separate authorities. Combat may
finish inside a player command before the next scheduled route wake. This
module closes that causal gap by replaying exactly one already-resolved player
contact through the existing route frontier, then merging only that route's
physical after-images back into the full world.

Legacy active contacts created before finite field-equipment materialization
also need one bounded pre-combat migration. The migration uses the same faction
armory and exact-holder machinery as a fresh route interception, and persists a
contact marker even when zero items are issued so later losses cannot trigger
mid-fight re-arming.

All helpers are deterministic and read-only until their caller commits the
returned after-images. Player-facing normalization never exposes opposing
person identities.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Callable, Mapping

from .faction_state import faction_path, hydrate_faction_state, inventory_path, roster_path
from .frontier_bridge import settle_shared_frontier
from .inventory_state import compact_inventory_state, hydrate_inventory_state
from .operational_equipment import materialize_faction_field_equipment
from .person_state import hydrate_roster_state
from .scheduler import route_ids_needing_service, sync_route_activity

_ROUTE_OPERATIONS = "state/martial-world/route-operations.json"
_COMBATS = "state/martial-world/combats.json"
_SCHEDULER = "state/martial-world/scheduler.json"
_EQUIPMENT_LEDGER = "state/martial-world/equipment-ledger.json"
_FIELD_EQUIPMENT_MARKER = "field_equipment_materialized_count"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _player_side(combat: Mapping[str, Any], player_ref: str) -> str | None:
    sides = _mapping(combat.get("sides"))
    for side_ref, members in sides.items():
        if isinstance(members, list) and player_ref in members:
            return str(side_ref)
    return None


def _resolved_player_contact(
    *,
    read_json: Callable[[str], Any],
    player_ref: str,
    combat_ref: str | None = None,
) -> tuple[str, dict[str, Any], str, dict[str, Any]] | None:
    route_state = read_json(_ROUTE_OPERATIONS)
    combat_state = read_json(_COMBATS)
    movements = _mapping(route_state.get("movements") if isinstance(route_state, Mapping) else None)
    combats = _mapping(combat_state.get("combats") if isinstance(combat_state, Mapping) else None)
    matches: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = []
    for movement_ref, raw_movement in movements.items():
        if not isinstance(movement_ref, str) or not isinstance(raw_movement, Mapping):
            continue
        if str(raw_movement.get("status") or "") != "contact_pending":
            continue
        participants = raw_movement.get("participant_refs")
        if not isinstance(participants, list) or player_ref not in participants:
            continue
        current_combat_ref = str(raw_movement.get("combat_ref") or "")
        if not current_combat_ref or (combat_ref is not None and current_combat_ref != combat_ref):
            continue
        raw_combat = combats.get(current_combat_ref)
        if not isinstance(raw_combat, Mapping) or str(raw_combat.get("status") or "") != "resolved":
            continue
        if _player_side(raw_combat, player_ref) is None:
            continue
        matches.append(
            (
                movement_ref,
                copy.deepcopy(dict(raw_movement)),
                current_combat_ref,
                copy.deepcopy(dict(raw_combat)),
            )
        )
    if len(matches) > 1:
        raise ValueError("multiple resolved player route contacts require reconciliation")
    return matches[0] if matches else None


def has_resolved_player_route_contact(
    *, read_json: Callable[[str], Any], player_ref: str, combat_ref: str | None = None,
) -> bool:
    """Return whether one exact player movement is stale behind resolved combat."""
    return _resolved_player_contact(
        read_json=read_json, player_ref=player_ref, combat_ref=combat_ref,
    ) is not None


def _active_route_contact(
    *, read_json: Callable[[str], Any], combat_ref: str,
) -> tuple[dict[str, Any], str, dict[str, Any], str, dict[str, Any], dict[str, Any]] | None:
    """Resolve one active route contact that owns ``combat_ref``.

    This is deliberately keyed by the exact combat identity supplied by the
    combat command. A non-route combat simply returns ``None``; malformed or
    multiply-owned route identity fails closed.
    """
    route_state = read_json(_ROUTE_OPERATIONS)
    combat_state = read_json(_COMBATS)
    if not isinstance(route_state, Mapping) or not isinstance(combat_state, Mapping):
        raise ValueError("route contact owners invalid")
    movements = _mapping(route_state.get("movements"))
    contacts = _mapping(route_state.get("contacts"))
    combats = _mapping(combat_state.get("combats"))
    combat = combats.get(combat_ref)
    if not isinstance(combat, Mapping) or str(combat.get("status") or "") != "active":
        return None

    matches: list[tuple[str, dict[str, Any]]] = []
    for movement_ref, raw_movement in movements.items():
        if not isinstance(movement_ref, str) or not isinstance(raw_movement, Mapping):
            continue
        if str(raw_movement.get("status") or "") != "contact_pending":
            continue
        if str(raw_movement.get("combat_ref") or "") != combat_ref:
            continue
        matches.append((movement_ref, copy.deepcopy(dict(raw_movement))))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("active route combat has ambiguous movement owner")

    movement_ref, movement = matches[0]
    contact_ref = str(movement.get("contact_ref") or "")
    raw_contact = contacts.get(contact_ref) if contact_ref else None
    if not contact_ref or not isinstance(raw_contact, Mapping):
        raise ValueError("active route combat contact owner missing")
    contact = copy.deepcopy(dict(raw_contact))
    if str(contact.get("combat_ref") or "") != combat_ref:
        raise ValueError("active route contact combat identity mismatch")
    if str(contact.get("movement_ref") or "") not in {"", movement_ref}:
        raise ValueError("active route contact movement identity mismatch")
    if str(contact.get("status") or "active") != "active":
        raise ValueError("active route contact status mismatch")
    return (
        copy.deepcopy(dict(route_state)),
        movement_ref,
        movement,
        contact_ref,
        contact,
        copy.deepcopy(dict(combat)),
    )


def _contact_attacker_identity(
    *, movement: Mapping[str, Any], contact: Mapping[str, Any], combat: Mapping[str, Any],
) -> tuple[str, list[str]]:
    """Validate the exact attacking faction and people already owned by the contact."""
    movement_faction = str(movement.get("contact_attacker_faction_ref") or "")
    contact_faction = str(contact.get("attacker_faction_ref") or "")
    if movement_faction and contact_faction and movement_faction != contact_faction:
        raise ValueError("route contact attacker faction mismatch")
    faction_ref = contact_faction or movement_faction
    if not faction_ref:
        raise ValueError("route contact attacker faction missing")

    movement_refs = movement.get("contact_attacker_refs")
    contact_refs = contact.get("attacker_refs")
    movement_list = [str(x) for x in movement_refs if isinstance(x, str) and x] if isinstance(movement_refs, list) else []
    contact_list = [str(x) for x in contact_refs if isinstance(x, str) and x] if isinstance(contact_refs, list) else []
    if movement_list and contact_list and set(movement_list) != set(contact_list):
        raise ValueError("route contact attacker roster mismatch")
    attacker_refs = list(dict.fromkeys(contact_list or movement_list))
    if not attacker_refs:
        raise ValueError("route contact attacker roster missing")

    participants = {
        str(x) for x in movement.get("participant_refs", [])
        if isinstance(x, str) and x
    } if isinstance(movement.get("participant_refs"), list) else set()
    if participants.intersection(attacker_refs):
        raise ValueError("route contact attacker overlaps protected movement")

    sides = _mapping(combat.get("sides"))
    containing_sides = []
    for side_ref, members in sides.items():
        member_set = {str(x) for x in members if isinstance(x, str) and x} if isinstance(members, list) else set()
        if set(attacker_refs).issubset(member_set):
            containing_sides.append(str(side_ref))
    if len(containing_sides) != 1:
        raise ValueError("route contact attacker combat side unresolved")
    return faction_ref, attacker_refs


def reconcile_active_route_contact_field_equipment_records(
    *, read_json: Callable[[str], Any], combat_ref: str,
) -> dict[str, Mapping[str, Any]]:
    """Stage a one-time finite armory migration for one legacy active contact.

    Fresh route contacts already persist ``field_equipment_materialized_count``
    before exact combat begins. Its *presence*, including a legitimate zero,
    proves that issuance was considered. Only older active contacts lacking the
    field are migrated. Persisting the marker in the same transaction as any
    inventory debit prevents later weapon loss, breakage, or ammunition use
    from being mistaken for a reason to issue replacement faction stock.
    """
    match = _active_route_contact(read_json=read_json, combat_ref=combat_ref)
    if match is None:
        return {}
    route_state, _movement_ref, movement, contact_ref, contact, combat = match
    if _FIELD_EQUIPMENT_MARKER in contact:
        return {}

    faction_ref, attacker_refs = _contact_attacker_identity(
        movement=movement, contact=contact, combat=combat,
    )

    fpath = faction_path(faction_ref)
    faction_raw = read_json(fpath)
    if not isinstance(faction_raw, Mapping) or str(faction_raw.get("faction_id") or "") != faction_ref:
        raise ValueError("route contact attacker faction owner invalid")
    faction = hydrate_faction_state(faction_raw)

    rpath = roster_path(faction_ref)
    roster_raw = read_json(rpath)
    if not isinstance(roster_raw, Mapping) or str(roster_raw.get("faction_ref") or "") != faction_ref:
        raise ValueError("route contact attacker roster owner invalid")
    roster = hydrate_roster_state(roster_raw, faction=faction)
    people = roster.get("people")
    if not isinstance(people, list):
        raise ValueError("route contact attacker roster invalid")
    people_by_ref = {
        str(row.get("person_id")): row
        for row in people
        if isinstance(row, Mapping) and isinstance(row.get("person_id"), str) and row.get("person_id")
    }
    if any(ref not in people_by_ref for ref in attacker_refs):
        raise ValueError("route contact attacker missing from faction roster")

    ipath = inventory_path(faction_ref)
    inventory_raw = read_json(ipath)
    if not isinstance(inventory_raw, Mapping) or str(inventory_raw.get("faction_ref") or "") != faction_ref:
        raise ValueError("route contact attacker inventory owner invalid")
    inventory = hydrate_inventory_state(inventory_raw)
    equipment_ledger = read_json(_EQUIPMENT_LEDGER)
    if not isinstance(equipment_ledger, Mapping):
        raise ValueError("route contact equipment ledger invalid")

    materialized = materialize_faction_field_equipment(
        faction_ref=faction_ref,
        participant_refs=attacker_refs,
        people_by_ref=people_by_ref,
        inventory=inventory,
        equipment_ledger=equipment_ledger,
        status="route_attack_field_issue",
    )
    count = max(0, int(materialized.get("materialized_person_count", 0)))
    inventory_after = compact_inventory_state(materialized.get("inventory_after", {}))
    ledger_after = materialized.get("equipment_ledger_after")
    if not isinstance(ledger_after, Mapping):
        raise ValueError("route contact equipment materialization invalid")

    route_after = copy.deepcopy(route_state)
    contacts_after = route_after.get("contacts")
    if not isinstance(contacts_after, dict):
        raise ValueError("route contact registry invalid")
    persisted_contact = contacts_after.get(contact_ref)
    if not isinstance(persisted_contact, Mapping):
        raise ValueError("route contact disappeared during materialization")
    contact_after = copy.deepcopy(dict(persisted_contact))
    contact_after[_FIELD_EQUIPMENT_MARKER] = count
    contacts_after[contact_ref] = contact_after

    writes: dict[str, Mapping[str, Any]] = {_ROUTE_OPERATIONS: route_after}
    if inventory_after != inventory_raw:
        writes[ipath] = inventory_after
    if dict(ledger_after) != dict(equipment_ledger):
        writes[_EQUIPMENT_LEDGER] = copy.deepcopy(dict(ledger_after))
    return writes


def _scoped_route_state(
    full_route: Mapping[str, Any], *, movement_ref: str, contact_ref: str,
) -> dict[str, Any]:
    out = copy.deepcopy(dict(full_route))
    movements = _mapping(full_route.get("movements"))
    movement = movements.get(movement_ref)
    if not isinstance(movement, Mapping):
        raise ValueError("resolved route contact movement missing")
    out["movements"] = {movement_ref: copy.deepcopy(dict(movement))}
    contacts = _mapping(full_route.get("contacts"))
    scoped_contacts: dict[str, Any] = {}
    if contact_ref:
        row = contacts.get(contact_ref)
        if isinstance(row, Mapping):
            scoped_contacts[contact_ref] = copy.deepcopy(dict(row))
    out["contacts"] = scoped_contacts
    return out


def _scheduler_without_segment_wake(
    schedule: Mapping[str, Any], *, movement_ref: str,
) -> dict[str, Any]:
    out = copy.deepcopy(dict(schedule))
    rows = out.get("one_off")
    if not isinstance(rows, dict):
        raise ValueError("scheduler one_off invalid")
    for event_id, raw in list(rows.items()):
        if not isinstance(raw, Mapping):
            continue
        if (
            str(raw.get("kind") or "") == "route_activity_cycle"
            and raw.get("exact_segment_due") is True
            and str(raw.get("movement_ref") or "") == movement_ref
        ):
            rows.pop(event_id, None)
    return out


def _merge_route_after(
    *,
    full_before: Mapping[str, Any],
    scoped_before: Mapping[str, Any],
    scoped_after: Mapping[str, Any],
    movement_ref: str,
    contact_ref: str,
) -> dict[str, Any]:
    full_after = copy.deepcopy(dict(full_before))
    full_movements = full_after.setdefault("movements", {})
    full_contacts = full_after.setdefault("contacts", {})
    if not isinstance(full_movements, dict) or not isinstance(full_contacts, dict):
        raise ValueError("route operations invalid")

    scoped_before_movements = _mapping(scoped_before.get("movements"))
    scoped_after_movements = _mapping(scoped_after.get("movements"))
    full_movements.pop(movement_ref, None)
    for ref, row in scoped_after_movements.items():
        if not isinstance(ref, str) or not isinstance(row, Mapping):
            continue
        if ref != movement_ref and ref not in scoped_before_movements:
            existing = full_movements.get(ref)
            if existing is not None and existing != row:
                raise ValueError("route reconciliation movement identity conflict")
        full_movements[ref] = copy.deepcopy(dict(row))

    if contact_ref:
        full_contacts.pop(contact_ref, None)
    scoped_after_contacts = _mapping(scoped_after.get("contacts"))
    for ref, row in scoped_after_contacts.items():
        if not isinstance(ref, str) or not isinstance(row, Mapping):
            continue
        existing = full_contacts.get(ref)
        if ref != contact_ref and existing is not None and existing != row:
            raise ValueError("route reconciliation contact identity conflict")
        full_contacts[ref] = copy.deepcopy(dict(row))

    ignored = {"movements", "contacts"}
    before_keys = set(scoped_before) - ignored
    after_keys = set(scoped_after) - ignored
    for key in sorted(before_keys | after_keys):
        before_value = scoped_before.get(key)
        after_value = scoped_after.get(key)
        if before_value == after_value:
            continue
        if key in scoped_after:
            full_after[key] = copy.deepcopy(after_value)
        else:
            full_after.pop(key, None)
    return full_after


def reconcile_resolved_player_route_contact_records(
    *,
    read_json: Callable[[str], Any],
    at: datetime,
    player_ref: str,
    combat_ref: str | None = None,
) -> dict[str, Mapping[str, Any]]:
    """Return atomic after-images that close one already-resolved player contact.

    The route frontier is replayed against a route-state projection containing
    only the affected movement. Other routes therefore cannot progress, roll
    encounters, or consume resources merely because combat ended. The full
    scheduler is retained, except the target movement's stale exact-segment wake
    is removed before replay so the authoritative reducer can rebase it from the
    combat-end timestamp.
    """
    match = _resolved_player_contact(
        read_json=read_json, player_ref=player_ref, combat_ref=combat_ref,
    )
    if match is None:
        return {}
    movement_ref, movement, resolved_combat_ref, _combat = match
    route_ref = str(movement.get("route_ref") or "")
    contact_ref = str(movement.get("contact_ref") or "")
    if not route_ref:
        raise ValueError("resolved route contact route missing")

    full_route = read_json(_ROUTE_OPERATIONS)
    schedule = read_json(_SCHEDULER)
    if not isinstance(full_route, Mapping) or not isinstance(schedule, Mapping):
        raise ValueError("route reconciliation owners invalid")
    scoped_route = _scoped_route_state(
        full_route, movement_ref=movement_ref, contact_ref=contact_ref,
    )
    scoped_schedule = _scheduler_without_segment_wake(
        schedule, movement_ref=movement_ref,
    )

    def scoped_read(path: str) -> Any:
        if path == _ROUTE_OPERATIONS:
            return copy.deepcopy(scoped_route)
        if path == _SCHEDULER:
            return copy.deepcopy(scoped_schedule)
        return copy.deepcopy(read_json(path))

    synthetic_event = {
        "event_id": f"route_contact_resolution:{resolved_combat_ref}",
        "kind": "route_activity_cycle",
        "due_at": at.isoformat(),
        "owner_ref": route_ref,
        "movement_ref": movement_ref,
        "requires_player_decision": False,
    }
    frontier = settle_shared_frontier(
        read_json=scoped_read,
        schedule=scoped_schedule,
        events=[synthetic_event],
        at=at,
    )
    raw_writes = frontier.get("writes") if isinstance(frontier, Mapping) else None
    if not isinstance(raw_writes, Mapping):
        raise ValueError("route reconciliation frontier writes invalid")
    writes: dict[str, Mapping[str, Any]] = {
        str(path): copy.deepcopy(dict(row))
        for path, row in raw_writes.items()
        if isinstance(path, str) and isinstance(row, Mapping)
    }
    scoped_route_after = writes.get(_ROUTE_OPERATIONS, scoped_route)
    if not isinstance(scoped_route_after, Mapping):
        raise ValueError("route reconciliation after-image invalid")
    merged_route = _merge_route_after(
        full_before=full_route,
        scoped_before=scoped_route,
        scoped_after=scoped_route_after,
        movement_ref=movement_ref,
        contact_ref=contact_ref,
    )
    writes[_ROUTE_OPERATIONS] = merged_route

    scoped_schedule_after = writes.get(_SCHEDULER, scoped_schedule)
    if not isinstance(scoped_schedule_after, Mapping):
        raise ValueError("route reconciliation scheduler after-image invalid")
    movements_after = _mapping(merged_route.get("movements"))
    merged_schedule = sync_route_activity(
        scoped_schedule_after,
        active_route_ids=route_ids_needing_service(movements_after),
        now=at,
    )
    writes[_SCHEDULER] = merged_schedule
    return writes


def normalize_resolved_route_contact_context(
    context: Mapping[str, Any], read_json: Callable[[str], Any],
) -> dict[str, Any]:
    """Retire a stale hostile-contact choice without pretending state was written.

    This is a player-safe projection only. It exposes no opposing person IDs and
    labels reconciliation as pending until a normal gameplay transaction commits
    the route owner's authoritative after-images.
    """
    out = copy.deepcopy(dict(context))
    campaign = _mapping(out.get("campaign"))
    player = _mapping(out.get("player"))
    player_ref = str(campaign.get("player_id") or player.get("person_id") or "")
    if not player_ref:
        return out
    match = _resolved_player_contact(read_json=read_json, player_ref=player_ref)
    if match is None:
        return out
    movement_ref, movement, _combat_ref, combat = match

    scene = out.get("scene")
    if not isinstance(scene, Mapping):
        return out
    movement_context = _mapping(scene.get("movement_context"))
    if str(movement_context.get("movement_ref") or "") != movement_ref:
        return out

    player_side = _player_side(combat, player_ref)
    winner = str(combat.get("winner_side") or "")
    if player_side is not None and winner == player_side:
        outcome = "victory"
    elif winner and winner != "none":
        outcome = "defeat"
    else:
        outcome = "inconclusive"
    safe_resolution = {
        "movement_ref": movement_ref,
        "combat_status": "resolved",
        "player_outcome": outcome,
        "elapsed_ms": max(0, int(combat.get("elapsed_ms", 0))),
        "reconciliation_pending": True,
        "knowledge_rule": (
            "This proves only that the player's route combat is already resolved; "
            "it does not expose opposing exact identities or reconstruct missing combat events."
        ),
    }

    scene_after = copy.deepcopy(dict(scene))
    handoff = scene_after.get("activity_handoff")
    if isinstance(handoff, Mapping) and str(handoff.get("kind") or "") == "hostile_contact":
        handoff_after = copy.deepcopy(dict(handoff))
        handoff_after["requires_player_decision"] = False
        handoff_after["handoff_status"] = "superseded_by_resolved_combat"
        handoff_after["reconciliation_pending"] = True
        scene_after["activity_handoff"] = handoff_after
    scene_after["resolved_route_contact"] = copy.deepcopy(safe_resolution)
    out["scene"] = scene_after

    gm = out.get("gm_scene_context")
    if isinstance(gm, Mapping):
        gm_after = copy.deepcopy(dict(gm))
        direction = gm_after.get("scene_direction")
        if isinstance(direction, Mapping):
            direction_after = copy.deepcopy(dict(direction))
            direction_after["protected_player_decision_pending"] = False
            direction_after["narrative_stage_hint"] = "aftermath_bridge"
            risks = direction_after.get("close_risks")
            if isinstance(risks, list):
                direction_after["close_risks"] = [
                    row for row in risks if str(row) != "protected_player_decision"
                ]
            gm_after["scene_direction"] = direction_after
        evidence = gm_after.get("wei_observations_and_known_scene_evidence")
        evidence_after = copy.deepcopy(dict(evidence)) if isinstance(evidence, Mapping) else {}
        evidence_after["resolved_route_contact"] = copy.deepcopy(safe_resolution)
        gm_after["wei_observations_and_known_scene_evidence"] = evidence_after
        out["gm_scene_context"] = gm_after
    return out


__all__ = [
    "has_resolved_player_route_contact",
    "normalize_resolved_route_contact_context",
    "reconcile_active_route_contact_field_equipment_records",
    "reconcile_resolved_player_route_contact_records",
]
