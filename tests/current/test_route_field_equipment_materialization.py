from __future__ import annotations

from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.operational_equipment import materialize_faction_field_equipment
from shinobi_runtime.martial_world.property import provenance_claim


def _fighter(ref: str) -> dict:
    return {
        "person_id": ref,
        "martial_skills": {
            "spear": 90,
            "sword": 5,
            "bow": 0,
            "hidden_weapons": 0,
            "unarmed": 25,
        },
    }


def test_route_field_issue_is_finite_conserved_and_idempotent():
    faction_ref = "faction.synthetic_field_company"
    refs = ["fighter.1", "fighter.2", "fighter.3"]
    people = {ref: _fighter(ref) for ref in refs}
    inventory = {"equipment": {"weapon_staff": 2}}
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {},
    }

    first = materialize_faction_field_equipment(
        faction_ref=faction_ref,
        participant_refs=refs,
        people_by_ref=people,
        inventory=inventory,
        equipment_ledger=ledger,
        status="test_field_issue",
    )
    assert first["materialized_person_count"] == 2
    assert first["materialized_item_count"] == 2
    assert first["inventory_after"].get("equipment", {}).get("weapon_staff", 0) == 0

    ledger_after = first["equipment_ledger_after"]
    armed = []
    unarmed = []
    for ref in refs:
        items = effective_person_loadout(ledger_after, ref).get("items", {})
        if int(items.get("weapon_staff", 0)) > 0:
            armed.append(ref)
            claim = provenance_claim(ledger_after, ref, "weapon_staff")
            assert claim is not None
            assert claim["owner_ref"] == faction_ref
            assert claim["quantity"] == 1
        else:
            unarmed.append(ref)
    assert len(armed) == 2
    assert len(unarmed) == 1

    second = materialize_faction_field_equipment(
        faction_ref=faction_ref,
        participant_refs=refs,
        people_by_ref=people,
        inventory=first["inventory_after"],
        equipment_ledger=ledger_after,
        status="test_field_issue_retry",
    )
    assert second["materialized_person_count"] == 0
    assert second["materialized_item_count"] == 0
    assert second["inventory_after"] == first["inventory_after"]
    for ref in armed:
        assert effective_person_loadout(second["equipment_ledger_after"], ref)["items"]["weapon_staff"] == 1


def test_existing_personal_weapon_is_not_replaced_or_double_debited():
    faction_ref = "faction.synthetic_field_company"
    people = {"fighter.1": _fighter("fighter.1"), "fighter.2": _fighter("fighter.2")}
    inventory = {"equipment": {"weapon_staff": 1}}
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {
            "fighter.1": {"items": {"weapon_spear": 1}, "condition_milli": {"weapon_spear": 1000}},
        },
    }
    result = materialize_faction_field_equipment(
        faction_ref=faction_ref,
        participant_refs=["fighter.1", "fighter.2"],
        people_by_ref=people,
        inventory=inventory,
        equipment_ledger=ledger,
    )
    assert result["materialized_person_count"] == 1
    assert effective_person_loadout(result["equipment_ledger_after"], "fighter.1")["items"] == {"weapon_spear": 1}
    assert effective_person_loadout(result["equipment_ledger_after"], "fighter.2")["items"]["weapon_staff"] == 1
    assert result["inventory_after"].get("equipment", {}).get("weapon_staff", 0) == 0
