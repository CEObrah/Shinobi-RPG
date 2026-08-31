from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected one source block in {path}, found {text.count(old)}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


operational = ROOT / "runtime/shinobi_runtime/martial_world/operational_equipment.py"
replace_once(
    operational,
    '_OPERATIONAL_KINDS = frozenset({"faction_raid", "faction_war_strike", "custody_rescue", "faction_reconnaissance", "allied_defense_reinforcement"})\n',
    '_OPERATIONAL_KINDS = frozenset({"faction_raid", "faction_war_strike", "custody_rescue", "faction_reconnaissance", "allied_defense_reinforcement", "route_attack"})\n',
)

anchor = '\n\ndef detach_operation_issue_holders(\n'
text = operational.read_text(encoding="utf-8")
if text.count(anchor) != 1:
    raise RuntimeError("operational equipment detach anchor missing")
helper = r'''

def materialize_faction_field_equipment(
    *,
    faction_ref: str,
    participant_refs: Sequence[str],
    people_by_ref: Mapping[str, Mapping[str, Any]],
    inventory: Mapping[str, Any],
    equipment_ledger: Mapping[str, Any],
    status: str = "field_issue_in_custody",
) -> dict[str, Any]:
    """Conservatively materialize aggregate armory stock onto exact field holders.

    This is for bounded contacts that do not own a long-lived deployment row,
    such as a spontaneous route interception. It reuses the same finite issue
    policy as strategic operations, then immediately detaches the short-lived
    return obligation into explicit source-faction title. Physical items remain
    on the exact people who carry them; nothing teleports back when the contact
    ends and nothing is created from skill or faction identity alone.

    Re-entry is naturally idempotent: already armed people are skipped by
    ``issue_operation_equipment`` and aggregate stock is debited only for newly
    materialized exact items.
    """
    synthetic_owner = {"operation_kind": "route_attack"}
    issued = issue_operation_equipment(
        operation=synthetic_owner,
        faction_ref=faction_ref,
        participant_refs=participant_refs,
        people_by_ref=people_by_ref,
        inventory=inventory,
        equipment_ledger=equipment_ledger,
    )
    issued_count = max(0, int(issued.get("issued_person_count", 0)))
    issued_item_count = max(0, int(issued.get("issued_item_count", 0)))
    ledger_after = issued["equipment_ledger_after"]
    if issued_count <= 0:
        return {
            "inventory_after": copy.deepcopy(dict(issued["inventory_after"])),
            "equipment_ledger_after": copy.deepcopy(dict(ledger_after)),
            "materialized_person_count": 0,
            "materialized_item_count": 0,
        }
    detached = detach_operation_issue_holders(
        operation=issued["operation_after"],
        source_faction_ref=faction_ref,
        holder_refs=participant_refs,
        equipment_ledger=ledger_after,
        status=status,
    )
    operation_after = detached.get("operation_after", {})
    if isinstance(operation_after, Mapping) and operation_after.get("issued_equipment"):
        raise ValueError("field issue retained transient return obligation")
    return {
        "inventory_after": copy.deepcopy(dict(issued["inventory_after"])),
        "equipment_ledger_after": copy.deepcopy(dict(detached["equipment_ledger_after"])),
        "materialized_person_count": issued_count,
        "materialized_item_count": issued_item_count,
    }
'''
operational.write_text(text.replace(anchor, helper + anchor, 1), encoding="utf-8")

route = ROOT / "runtime/shinobi_runtime/martial_world/route_frontier.py"
replace_once(
    route,
    'from .operational_equipment import detach_operation_issue_holders, reclaim_operation_equipment\n',
    'from .operational_equipment import detach_operation_issue_holders, materialize_faction_field_equipment, reclaim_operation_equipment\n',
)

route_anchor = '''        def _update_roster_people(fid: str, people_after: Mapping[str, Mapping[str, Any]]) -> None:\n'''
route_text = route.read_text(encoding="utf-8")
if route_text.count(route_anchor) != 1:
    raise RuntimeError("route field issue helper anchor missing")
route_helper = r'''        def _materialize_route_attack_field_equipment(
            faction_ref: str, participant_refs: Sequence[str], people_by_ref: Mapping[str, Mapping[str, Any]],
        ) -> int:
            """Cross aggregate armory stock into exact holder custody before contact."""
            nonlocal equipment_ledger
            refs = [str(ref) for ref in participant_refs if isinstance(ref, str) and ref]
            if not faction_ref or not refs:
                return 0
            ipath, inventory = load_inventory(faction_ref)
            materialized = materialize_faction_field_equipment(
                faction_ref=faction_ref,
                participant_refs=refs,
                people_by_ref=people_by_ref,
                inventory=inventory,
                equipment_ledger=equipment_ledger,
                status="route_attack_field_issue",
            )
            count = max(0, int(materialized.get("materialized_person_count", 0)))
            if count <= 0:
                return 0
            inventory_after = copy.deepcopy(dict(materialized["inventory_after"]))
            equipment_ledger = copy.deepcopy(dict(materialized["equipment_ledger_after"]))
            writes[ipath] = inventory_after
            inventory_cache[faction_ref] = (ipath, inventory_after)
            writes[_EQUIPMENT_LEDGER_PATH] = equipment_ledger
            return count

'''
route.write_text(route_text.replace(route_anchor, route_helper + route_anchor, 1), encoding="utf-8")

replace_once(
    route,
    '                    contact_ref = f"contact:{movement_ref}:{at.date().isoformat()}:{attacker_fid}"\n                    if beneficiary:\n',
    '                    contact_ref = f"contact:{movement_ref}:{at.date().isoformat()}:{attacker_fid}"\n                    field_equipment_materialized_count = _materialize_route_attack_field_equipment(\n                        attacker_fid, attacker_refs, people\n                    )\n                    if beneficiary:\n',
)
replace_once(
    route,
    '                            "gm_private_decision_context": private_interception_decision_context(decision),\n                        }\n',
    '                            "gm_private_decision_context": private_interception_decision_context(decision),\n                            "field_equipment_materialized_count": field_equipment_materialized_count,\n                        }\n',
)
replace_once(
    route,
    '                            "attacker_faction_ref": attacker_fid, "requires_player_decision": True,\n                            "delivered_to_player": True,\n',
    '                            "attacker_faction_ref": attacker_fid, "requires_player_decision": True,\n                            "field_equipment_materialized_count": field_equipment_materialized_count,\n                            "delivered_to_player": True,\n',
)

# Generic finite-stock/provenance regression. No campaign-specific faction IDs.
test = ROOT / "tests/current/test_route_field_equipment_materialization.py"
test.write_text(r'''from __future__ import annotations

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
''', encoding="utf-8")

print("route field equipment patch applied")
