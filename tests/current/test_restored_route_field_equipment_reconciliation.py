from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner, _RecordReadView
from shinobi_runtime.commands.core import _BuiltPlan
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.faction_state import inventory_path as canonical_inventory_path
from shinobi_runtime.martial_world.property import provenance_claim
from shinobi_runtime.martial_world.route_field_equipment_reconciliation import (
    COMBATS_PATH,
    EQUIPMENT_LEDGER_PATH,
    FIELD_EQUIPMENT_MARKER,
    ROUTE_OPERATIONS_PATH,
    restored_route_field_equipment_records,
)


COMBAT_REF = "combat:contact:movement.test:0061-09-27:synthetic_field_company"
CONTACT_REF = "contact:movement.test:0061-09-27:synthetic_field_company"
FACTION_REF = "faction.synthetic_field_company"
ATTACKERS = ["attacker.1", "attacker.2", "attacker.3"]


def _person(ref: str) -> dict:
    return {
        "person_id": ref,
        "faction_ref": FACTION_REF,
        "martial_skills": {
            "spear": 90,
            "sword": 5,
            "bow": 0,
            "hidden_weapons": 0,
            "unarmed": 25,
        },
    }


def _records(*, staff_stock: int = 2, marker: int | None = None) -> dict[str, dict]:
    contact = {
        "status": "active",
        "combat_ref": COMBAT_REF,
        "attacker_faction_ref": FACTION_REF,
        "attacker_refs": list(ATTACKERS),
    }
    if marker is not None:
        contact[FIELD_EQUIPMENT_MARKER] = marker
    return {
        ROUTE_OPERATIONS_PATH: {
            "schema": "jianghu-route-operations-state-1.0",
            "movements": {
                "movement.test": {
                    "status": "contact_pending",
                    "combat_ref": COMBAT_REF,
                    "contact_ref": CONTACT_REF,
                    "contact_attacker_faction_ref": FACTION_REF,
                    "contact_attacker_refs": list(ATTACKERS),
                },
            },
            "contacts": {CONTACT_REF: contact},
        },
        COMBATS_PATH: {
            "combats": {
                COMBAT_REF: {
                    "status": "active",
                    "sides": {"side_a": ["wei"], "side_b": list(ATTACKERS)},
                }
            }
        },
        EQUIPMENT_LEDGER_PATH: {
            "schema": "jianghu-equipment-ledger-1.0",
            "policy_assignments": {},
            "person_loadouts": {},
        },
        canonical_inventory_path(FACTION_REF): {"equipment": {"weapon_staff": staff_stock}},
    }


def test_restored_route_contact_materializes_only_finite_faction_stock_and_stamps_contact():
    records = _records(staff_stock=2)
    result = restored_route_field_equipment_records(
        read_json=lambda path: copy.deepcopy(records[path]),
        resolve_person=lambda ref: _person(ref),
        combat_ref=COMBAT_REF,
    )

    inventory_path = canonical_inventory_path(FACTION_REF)
    assert result[ROUTE_OPERATIONS_PATH]["contacts"][CONTACT_REF][FIELD_EQUIPMENT_MARKER] == 2
    assert result[inventory_path].get("equipment", {}).get("weapon_staff", 0) == 0

    ledger = result[EQUIPMENT_LEDGER_PATH]
    armed = []
    for ref in ATTACKERS:
        loadout = effective_person_loadout(ledger, ref)
        if int(loadout.get("items", {}).get("weapon_staff", 0)) > 0:
            armed.append(ref)
            claim = provenance_claim(ledger, ref, "weapon_staff")
            assert claim is not None
            assert claim["owner_ref"] == FACTION_REF
            assert int(claim["quantity"]) == 1
    assert len(armed) == 2


def test_restored_route_contact_marker_makes_compatibility_reentry_a_noop():
    records = _records(staff_stock=2)
    first = restored_route_field_equipment_records(
        read_json=lambda path: copy.deepcopy(records[path]),
        resolve_person=lambda ref: _person(ref),
        combat_ref=COMBAT_REF,
    )
    records.update({path: copy.deepcopy(row) for path, row in first.items()})

    second = restored_route_field_equipment_records(
        read_json=lambda path: copy.deepcopy(records[path]),
        resolve_person=lambda ref: _person(ref),
        combat_ref=COMBAT_REF,
    )
    assert second == {}


def test_zero_stock_is_still_stamped_so_later_inventory_cannot_rearm_the_fight():
    records = _records(staff_stock=0)
    first = restored_route_field_equipment_records(
        read_json=lambda path: copy.deepcopy(records[path]),
        resolve_person=lambda ref: _person(ref),
        combat_ref=COMBAT_REF,
    )
    assert first[ROUTE_OPERATIONS_PATH]["contacts"][CONTACT_REF][FIELD_EQUIPMENT_MARKER] == 0
    records.update({path: copy.deepcopy(row) for path, row in first.items()})
    records[canonical_inventory_path(FACTION_REF)] = {"equipment": {"weapon_staff": 3}}

    assert restored_route_field_equipment_records(
        read_json=lambda path: copy.deepcopy(records[path]),
        resolve_person=lambda ref: _person(ref),
        combat_ref=COMBAT_REF,
    ) == {}


def test_modern_route_contact_with_field_marker_is_unchanged():
    records = _records(staff_stock=2, marker=2)
    assert restored_route_field_equipment_records(
        read_json=lambda path: copy.deepcopy(records[path]),
        resolve_person=lambda ref: _person(ref),
        combat_ref=COMBAT_REF,
    ) == {}


def test_production_combat_wrapper_stages_field_equipment_before_base_resolution(monkeypatch):
    records = _records(staff_stock=2)

    class FakeRepository:
        def read_json(self, path):
            return copy.deepcopy(records[path])

        def read_optional_bytes(self, path):
            row = records.get(str(path))
            if row is None:
                return None
            return json.dumps(row, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"

        def read_bytes(self, path):
            raw = self.read_optional_bytes(path)
            if raw is None:
                raise FileNotFoundError(str(path))
            return raw

    planner = object.__new__(CampaignCommandPlanner)
    planner.repository = FakeRepository()

    def fake_person(ref):
        person = _person(ref) if ref in ATTACKERS else {"person_id": ref, "faction_ref": "house_tang"}
        return ("unused", {}, 0, person)

    planner._person = fake_person
    observed = {}

    def fake_base(self, command, meta, current_time):
        assert isinstance(self.repository, _RecordReadView)
        ledger = self.repository.read_json(EQUIPMENT_LEDGER_PATH)
        observed["armed_count"] = sum(
            1 for ref in ATTACKERS
            if int(effective_person_loadout(ledger, ref).get("items", {}).get("weapon_staff", 0)) > 0
        )
        writes = {
            path: json.dumps(self.repository.read_json(path), ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
            for path in (ROUTE_OPERATIONS_PATH, canonical_inventory_path(FACTION_REF), EQUIPMENT_LEDGER_PATH)
        }
        return _BuiltPlan(
            code="fake",
            affected_refs=tuple(sorted(writes)),
            writes=writes,
            result={"combat_status": "active", "combat_ref": COMBAT_REF},
            validator=lambda overlay, manifest: None,
        )

    monkeypatch.setattr(RepositoryCommandPlanner, "_jianghu_combat_resolution", fake_base)
    command = SimpleNamespace(payload={"action": "exchange", "combat_ref": COMBAT_REF}, actor_id="wei")
    plan = planner._jianghu_combat_resolution(command, {}, SimpleNamespace())

    assert observed["armed_count"] == 2
    assert plan.result["route_field_equipment_reconciled"] is True
    assert planner.repository.__class__ is FakeRepository
