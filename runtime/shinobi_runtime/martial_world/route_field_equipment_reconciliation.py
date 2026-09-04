"""Compatibility reconciliation for restored route combats missing field issue.

Fresh route contacts materialize finite faction armory stock before exact combat is
created. Historical/restored contacts can predate that handoff. This module
bridges only that legacy gap: it proves one active route contact owns the combat,
issues finite source-faction stock to its exact attacker refs, and stamps the
contact so the compatibility handoff can never re-arm the same fight later.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping

from .equipment_state import compact_equipment_ledger
from .faction_state import inventory_path as canonical_inventory_path
from .operational_equipment import materialize_faction_field_equipment

COMBATS_PATH = "state/martial-world/combats.json"
EQUIPMENT_LEDGER_PATH = "state/martial-world/equipment-ledger.json"
ROUTE_OPERATIONS_PATH = "state/martial-world/route-operations.json"
FIELD_EQUIPMENT_MARKER = "field_equipment_materialized_count"


def restored_route_field_equipment_records(
    *,
    read_json: Callable[[str], Any],
    resolve_person: Callable[[str], Mapping[str, Any]],
    combat_ref: str,
) -> dict[str, Mapping[str, Any]]:
    """Return atomic compatibility records for one legacy active route combat.

    Non-route combats and modern contacts that already carry the marker are a
    no-op. Contradictory route/contact provenance fails closed. The marker is
    persisted even when no stock can be issued, so later weapon loss/breakage
    cannot turn compatibility into a repeated rearm mechanic.
    """
    if not isinstance(combat_ref, str) or not combat_ref:
        raise ValueError("route combat ref invalid")

    route_ops = read_json(ROUTE_OPERATIONS_PATH)
    if not isinstance(route_ops, Mapping):
        raise ValueError("route operations invalid")
    movements = route_ops.get("movements", {})
    contacts = route_ops.get("contacts", {})
    if not isinstance(movements, Mapping) or not isinstance(contacts, Mapping):
        raise ValueError("route operations owners invalid")

    matching = [
        (str(movement_ref), movement)
        for movement_ref, movement in movements.items()
        if isinstance(movement_ref, str)
        and isinstance(movement, Mapping)
        and movement.get("status") == "contact_pending"
        and movement.get("combat_ref") == combat_ref
    ]
    if not matching:
        return {}
    if len(matching) != 1:
        raise ValueError("route combat movement provenance ambiguous")
    _movement_ref, movement = matching[0]

    contact_ref = str(movement.get("contact_ref") or "")
    if not contact_ref:
        raise ValueError("route combat contact ref missing")
    contact = contacts.get(contact_ref)
    if not isinstance(contact, Mapping):
        raise ValueError("route combat contact missing")
    if contact.get("status") != "active" or contact.get("combat_ref") != combat_ref:
        raise ValueError("route combat contact provenance mismatch")
    if FIELD_EQUIPMENT_MARKER in contact:
        return {}

    faction_ref = str(
        contact.get("attacker_faction_ref")
        or movement.get("contact_attacker_faction_ref")
        or ""
    )
    raw_attackers = contact.get("attacker_refs")
    if not isinstance(raw_attackers, list):
        raw_attackers = movement.get("contact_attacker_refs")
    attacker_refs = [str(ref) for ref in raw_attackers if isinstance(ref, str) and ref] if isinstance(raw_attackers, list) else []
    if not faction_ref or not attacker_refs or len(set(attacker_refs)) != len(attacker_refs):
        raise ValueError("route combat attacker provenance invalid")

    combats = read_json(COMBATS_PATH)
    combat_rows = combats.get("combats", {}) if isinstance(combats, Mapping) else {}
    combat = combat_rows.get(combat_ref) if isinstance(combat_rows, Mapping) else None
    if not isinstance(combat, Mapping) or combat.get("status") != "active":
        raise ValueError("route combat owner unavailable")
    combat_refs = {
        str(ref)
        for refs in (combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}).values()
        if isinstance(refs, list)
        for ref in refs
        if isinstance(ref, str)
    }
    if not set(attacker_refs).issubset(combat_refs):
        raise ValueError("route attackers not contained in exact combat")

    people: dict[str, Mapping[str, Any]] = {}
    for ref in attacker_refs:
        person = resolve_person(ref)
        if not isinstance(person, Mapping) or str(person.get("faction_ref") or "") != faction_ref:
            raise ValueError("route attacker faction provenance mismatch")
        people[ref] = person

    inventory_path = canonical_inventory_path(faction_ref)
    inventory = read_json(inventory_path)
    ledger = read_json(EQUIPMENT_LEDGER_PATH)
    if not isinstance(inventory, Mapping) or not isinstance(ledger, Mapping):
        raise ValueError("route field equipment authority invalid")

    materialized = materialize_faction_field_equipment(
        faction_ref=faction_ref,
        participant_refs=attacker_refs,
        people_by_ref=people,
        inventory=inventory,
        equipment_ledger=ledger,
        status="route_attack_field_issue_compatibility",
    )
    count = max(0, int(materialized.get("materialized_person_count", 0)))

    route_after = copy.deepcopy(dict(route_ops))
    contact_rows = route_after.get("contacts")
    if not isinstance(contact_rows, dict):
        raise ValueError("route contact owner not mutable")
    contact_after = contact_rows.get(contact_ref)
    if not isinstance(contact_after, dict):
        raise ValueError("route contact row not mutable")
    contact_after[FIELD_EQUIPMENT_MARKER] = count

    return {
        ROUTE_OPERATIONS_PATH: route_after,
        inventory_path: copy.deepcopy(dict(materialized["inventory_after"])),
        EQUIPMENT_LEDGER_PATH: compact_equipment_ledger(materialized["equipment_ledger_after"]),
    }


__all__ = [
    "FIELD_EQUIPMENT_MARKER",
    "restored_route_field_equipment_records",
]
