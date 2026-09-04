import copy
import shutil
from pathlib import Path

import pytest

from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner
from shinobi_runtime.commands.core import _BuiltPlan
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.route_contact_reconciliation import (
    reconcile_active_route_contact_field_equipment_records,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]
ROUTE_PATH = "state/martial-world/route-operations.json"
COMBAT_PATH = "state/martial-world/combats.json"
EQUIPMENT_PATH = "state/martial-world/equipment-ledger.json"
FACTION_PATH = "state/martial-world/factions/black_lance_company.json"
ROSTER_PATH = "state/martial-world/people/black_lance_company.json"
INVENTORY_PATH = "state/martial-world/inventories/black_lance_company.json"
COMBAT_REF = "combat.test.legacy-route-field-equipment"
MOVEMENT_REF = "movement.test.legacy-route-field-equipment"
CONTACT_REF = "contact.test.legacy-route-field-equipment"
ATTACKERS = [
    "mw.person.black_lance_company.test001",
    "mw.person.black_lance_company.test002",
]


def _legacy_records():
    return {
        ROUTE_PATH: {
            "schema": "jianghu-route-operations-state-1.0",
            "movements": {
                MOVEMENT_REF: {
                    "status": "contact_pending",
                    "route_ref": "route.changan.huashan",
                    "participant_refs": ["pc_wei_tang"],
                    "combat_ref": COMBAT_REF,
                    "contact_ref": CONTACT_REF,
                    "contact_attacker_faction_ref": "black_lance_company",
                    "contact_attacker_refs": list(ATTACKERS),
                },
            },
            "contacts": {
                CONTACT_REF: {
                    "status": "active",
                    "movement_ref": MOVEMENT_REF,
                    "combat_ref": COMBAT_REF,
                    "attacker_faction_ref": "black_lance_company",
                    "attacker_refs": list(ATTACKERS),
                },
            },
        },
        COMBAT_PATH: {
            "schema": "jianghu-combats-state-1.0",
            "combats": {
                COMBAT_REF: {
                    "status": "active",
                    "sides": {
                        "side_a": ["pc_wei_tang"],
                        "side_b": list(ATTACKERS),
                    },
                },
            },
        },
        FACTION_PATH: {
            "faction_id": "black_lance_company",
        },
        ROSTER_PATH: {
            "faction_ref": "black_lance_company",
            "people": [
                {"person_id": ATTACKERS[0], "martial_skills": {"spear": 80}},
                {"person_id": ATTACKERS[1], "martial_skills": {"spear": 70}},
            ],
        },
        INVENTORY_PATH: {
            "schema": "jianghu-faction-inventory-1.0",
            "faction_ref": "black_lance_company",
            "equipment": {"weapon_spear": 2},
        },
        EQUIPMENT_PATH: {
            "schema": "jianghu-equipment-ledger-1.0",
            "person_loadouts": {},
        },
    }


def _reader(records):
    def read_json(path):
        if path not in records:
            raise FileNotFoundError(path)
        return copy.deepcopy(records[path])
    return read_json


def test_legacy_active_contact_materializes_finite_armory_and_persists_one_shot_marker():
    records = _legacy_records()
    writes = reconcile_active_route_contact_field_equipment_records(
        read_json=_reader(records), combat_ref=COMBAT_REF,
    )

    contact = writes[ROUTE_PATH]["contacts"][CONTACT_REF]
    assert contact["field_equipment_materialized_count"] == 2
    assert writes[INVENTORY_PATH].get("equipment", {}).get("weapon_spear", 0) == 0
    ledger = writes[EQUIPMENT_PATH]
    for ref in ATTACKERS:
        assert effective_person_loadout(ledger, ref)["items"].get("weapon_spear") == 1

    # Once the marker exists, later loss or fresh faction stock must not be
    # interpreted as permission to issue replacement weapons mid-fight.
    migrated = copy.deepcopy(records)
    migrated.update(copy.deepcopy(writes))
    migrated[INVENTORY_PATH] = copy.deepcopy(records[INVENTORY_PATH])
    migrated[INVENTORY_PATH]["equipment"]["weapon_spear"] = 1
    migrated[EQUIPMENT_PATH] = copy.deepcopy(ledger)
    migrated[EQUIPMENT_PATH].setdefault("person_loadouts", {})[ATTACKERS[0]] = {
        "items": {}, "condition_milli": {},
    }
    assert reconcile_active_route_contact_field_equipment_records(
        read_json=_reader(migrated), combat_ref=COMBAT_REF,
    ) == {}


def test_existing_zero_marker_is_authoritative_and_does_not_retry_issuance():
    records = _legacy_records()
    records[ROUTE_PATH]["contacts"][CONTACT_REF]["field_equipment_materialized_count"] = 0
    assert reconcile_active_route_contact_field_equipment_records(
        read_json=_reader(records), combat_ref=COMBAT_REF,
    ) == {}


def test_legacy_contact_identity_mismatch_fails_closed():
    records = _legacy_records()
    records[ROUTE_PATH]["contacts"][CONTACT_REF]["attacker_refs"] = [ATTACKERS[0]]
    with pytest.raises(ValueError, match="attacker roster mismatch"):
        reconcile_active_route_contact_field_equipment_records(
            read_json=_reader(records), combat_ref=COMBAT_REF,
        )


def _copy_live_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "runtime/contracts", root / "runtime/contracts")
    return root


def test_production_combat_stage_is_visible_to_base_reducer_before_exchange(tmp_path, monkeypatch):
    repo = RepositoryStore(_copy_live_repository(tmp_path))
    planner = CampaignCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    current_time = CampaignTime.parse(meta["time"])

    staged_route = copy.deepcopy(repo.read_json(ROUTE_PATH))
    staged_route["test_legacy_field_equipment_probe"] = "staged-before-exchange"
    staged_ledger = copy.deepcopy(repo.read_json(EQUIPMENT_PATH))
    staged_ledger["test_legacy_field_equipment_probe"] = "staged-before-exchange"
    staged = {ROUTE_PATH: staged_route, EQUIPMENT_PATH: staged_ledger}
    observed = {}

    def fake_base_exchange(self, command, reducer_meta, reducer_time):
        observed["route"] = self.repository.read_json(ROUTE_PATH).get("test_legacy_field_equipment_probe")
        observed["ledger"] = self.repository.read_json(EQUIPMENT_PATH).get("test_legacy_field_equipment_probe")
        return _BuiltPlan(
            code="test_combat_exchange",
            affected_refs=(),
            writes={},
            result={
                "command_type": "jianghu_combat_resolution",
                "combat_ref": "combat.test.stage-order",
                "combat_status": "active",
                "world_time": str(reducer_time),
            },
            validator=lambda overlay, manifest: None,
        )

    monkeypatch.setattr(
        RepositoryCommandPlanner, "_jianghu_combat_resolution", fake_base_exchange,
    )
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="test.legacy-field-equipment.stage-order",
        actor_id=meta["player_id"],
        command_type="jianghu_combat_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-09-04T12:00:00Z",
        payload={"action": "exchange", "combat_ref": "combat.test.stage-order"},
        mode="gameplay",
    )

    planner._combat_plan_with_staged_records(
        command, meta, current_time, staged,
    )
    assert observed == {
        "route": "staged-before-exchange",
        "ledger": "staged-before-exchange",
    }
    assert "test_legacy_field_equipment_probe" not in repo.read_json(ROUTE_PATH)
    assert "test_legacy_field_equipment_probe" not in repo.read_json(EQUIPMENT_PATH)
