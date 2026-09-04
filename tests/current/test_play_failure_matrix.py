from __future__ import annotations

import json
import copy
import pytest
from collections.abc import Mapping
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout, hydrate_equipment_ledger
from shinobi_runtime.martial_world.exact_combat import _ally_support_step
from shinobi_runtime.martial_world.live_state import roster_person
from shinobi_runtime.martial_world.property import provenance_claim
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]
PLAYER = "pc_wei_tang"
HAN = "mw.person.house_tang.1032"
YAO = "mw.person.house_tang.1045"


def _active_player_combat(repo: RepositoryStore) -> str:
    state = repo.read_json("state/martial-world/combats.json")
    for ref, row in state.get("combats", {}).items():
        if not isinstance(row, Mapping) or row.get("status") != "active":
            continue
        members = {
            str(member)
            for side in row.get("sides", {}).values()
            if isinstance(side, list)
            for member in side
            if isinstance(member, str)
        }
        if PLAYER in members:
            return str(ref)
    pytest.skip("supplied save no longer contains the historical active play-regression combat")


def test_current_save_attack_plus_medic_order_is_one_semantic_combat_action():
    """Regression for the played 'keep fighting; send Han to Yao' failure."""
    repo = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    combat_ref = _active_player_combat(repo)
    command = CommandEnvelope(
        meta["campaign_id"], "play-regression.attack-plus-medic", meta["player_id"],
        "jianghu_combat_resolution", meta["revision"], "2026-09-03T00:00:00Z",
        {
            "action": "exchange", "combat_ref": combat_ref, "duration_seconds": 1,
            "ally_orders": [{"actor_ref": HAN, "task": "treat", "target_ref": YAO}],
        }, mode="gameplay",
    )

    assert planner.preview(command).status == "ready"
    plan = planner.plan(command)
    player_events = [row for row in plan.result["events"] if row.get("actor_ref") == PLAYER]
    support_events = [
        row for row in plan.result["events"]
        if row.get("actor_ref") == HAN and row.get("action_kind") == "ally_support"
    ]
    assert player_events, "Wei's personal-combat half disappeared"
    assert support_events, "Han's simultaneous support order disappeared"
    assert all(row.get("decision_origin") == "player_ally_order" for row in support_events)
    assert all(row.get("intended_ref") == YAO for row in support_events)

    info = plan.result.get("combat_information")
    assert isinstance(info, Mapping)
    assert info.get("scale") == "exact_people"
    assert int(info.get("observed_hostiles_cumulative", 0)) >= int(info.get("observed_active_engaged", 0))
    for key in (
        "observed_active_engaged", "observed_withdrawing", "observed_escaped", "observed_dead",
        "player_confirmed_defeats_this_resolution", "player_confirmed_defeats_encounter",
    ):
        assert key in info

    staged = json.loads(plan.writes["state/martial-world/combats.json"])
    han_state = staged["combats"][combat_ref]["combatants"][HAN]
    assert han_state.get("support_task", {}).get("task") == "treat"
    assert han_state.get("support_task", {}).get("target_ref") == YAO
    assert han_state.get("support_task", {}).get("status") == "active"
    yao_position = staged["combats"][combat_ref]["positions"][YAO]
    assert yao_position.get("stance") == "fallen"
    assert int(yao_position.get("body_radius_mm", 9999)) <= 140


def test_field_medic_is_not_reassigned_to_assault_when_retinue_has_spare_fighter():
    """Static behavior guard for the second autonomous-medic attack path."""
    source = (ROOT / "runtime/shinobi_runtime/combat/team_tactics.py").read_text()
    assert "'preferred_action': 'medical_support_hold' if standing_role == 'field_medic' else 'attack'" in source
    assert "'role': 'medical_support' if standing_role == 'field_medic' else 'exploit'" in source


def test_current_field_medic_can_complete_conserved_emergency_stabilization():
    """Once Han physically reaches Yao, the support objective performs medicine.

    This uses the played current-save casualty and issued field kit, but runs on
    copies so it cannot advance or repair campaign truth during the regression.
    """
    repo = RepositoryStore(ROOT)
    combat_ref = _active_player_combat(repo)
    combat = copy.deepcopy(repo.read_json("state/martial-world/combats.json")["combats"][combat_ref])
    people = {}
    for ref in (HAN, YAO):
        _path, _roster, _ordinal, person = roster_person(repo, ref)
        people[ref] = copy.deepcopy(person)
    ledger = hydrate_equipment_ledger(repo.read_json("state/martial-world/equipment-ledger.json"))

    # Isolate the medical phase after the already-tested approach. Han is now
    # kneeling inside treatment reach of Yao's fallen body.
    combat["positions"][HAN]["x_mm"] = int(combat["positions"][YAO]["x_mm"]) - 500
    combat["positions"][HAN]["y_mm"] = int(combat["positions"][YAO]["y_mm"])
    start_ms = int(combat.get("elapsed_ms", 0))
    combat["combatants"][HAN]["support_task"] = {
        "task": "treat", "target_ref": YAO, "status": "active",
        "issued_by_ref": PLAYER, "issued_at_ms": start_ms,
    }
    before_bleeding = int(people[YAO]["health"]["injuries"][0]["bleeding_ml_per_min"])
    before_supply = int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"])

    begun = _ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms,
    )
    assert begun["result"] == "support_treatment_started"
    assert people[YAO]["health"]["injuries"][0]["bleeding_ml_per_min"] == before_bleeding

    completed = _ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms + 10_000,
    )
    assert completed["result"] == "support_treatment_completed"
    assert int(completed["treatment_score"]) > 0
    assert int(completed["medical_supply_consumed"]) == 1
    assert int(people[YAO]["health"]["injuries"][0]["bleeding_ml_per_min"]) < before_bleeding
    assert int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"]) == before_supply - 1
    claim = provenance_claim(ledger, HAN, "supply_medical_bundle")
    assert isinstance(claim, Mapping)
    assert claim["owner_ref"] == "house_tang"
    assert int(claim["quantity"]) == before_supply - 1
    assert combat["combatants"][HAN]["support_task"]["status"] == "completed"


def test_field_treatment_fails_closed_without_physician_kit_and_conserves_supply():
    """The role label alone cannot mint the missing medical instrument."""
    repo = RepositoryStore(ROOT)
    combat_ref = _active_player_combat(repo)
    combat = copy.deepcopy(repo.read_json("state/martial-world/combats.json")["combats"][combat_ref])
    people = {}
    for ref in (HAN, YAO):
        _path, _roster, _ordinal, person = roster_person(repo, ref)
        people[ref] = copy.deepcopy(person)
    ledger = hydrate_equipment_ledger(repo.read_json("state/martial-world/equipment-ledger.json"))
    combat["positions"][HAN]["x_mm"] = int(combat["positions"][YAO]["x_mm"]) - 500
    combat["positions"][HAN]["y_mm"] = int(combat["positions"][YAO]["y_mm"])
    start_ms = int(combat.get("elapsed_ms", 0))
    combat["combatants"][HAN]["support_task"] = {
        "task": "treat", "target_ref": YAO, "status": "active",
        "issued_by_ref": PLAYER, "issued_at_ms": start_ms,
    }
    load = ledger["person_loadouts"][HAN]
    load["items"].pop("tool_physicians_kit", None)
    load.get("condition_milli", {}).pop("tool_physicians_kit", None)
    before_supply = int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"])
    before_bleeding = sum(int(row.get("bleeding_ml_per_min", 0)) for row in people[YAO]["health"]["injuries"])

    begun = _ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms,
    )
    assert begun["result"] == "support_treatment_started"
    blocked = _ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms + 10_000,
    )
    assert blocked["result"] == "support_treatment_no_physician_kit"
    assert combat["combatants"][HAN]["support_task"]["status"] == "blocked"
    assert combat["combatants"][HAN]["support_task"]["blocked_reason"] == "physician_kit_unavailable"
    assert int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"]) == before_supply
    assert sum(int(row.get("bleeding_ml_per_min", 0)) for row in people[YAO]["health"]["injuries"]) == before_bleeding


def test_field_treatment_clock_restarts_after_combat_interruption():
    """Ten seconds near a casualty is not treatment if defense broke access."""
    repo = RepositoryStore(ROOT)
    combat_ref = _active_player_combat(repo)
    combat = copy.deepcopy(repo.read_json("state/martial-world/combats.json")["combats"][combat_ref])
    people = {}
    for ref in (HAN, YAO):
        _path, _roster, _ordinal, person = roster_person(repo, ref)
        people[ref] = copy.deepcopy(person)
    ledger = hydrate_equipment_ledger(repo.read_json("state/martial-world/equipment-ledger.json"))
    combat["positions"][HAN]["x_mm"] = int(combat["positions"][YAO]["x_mm"]) - 500
    combat["positions"][HAN]["y_mm"] = int(combat["positions"][YAO]["y_mm"])
    start_ms = int(combat.get("elapsed_ms", 0))
    combat["combatants"][HAN]["support_task"] = {
        "task": "treat", "target_ref": YAO, "status": "active",
        "issued_by_ref": PLAYER, "issued_at_ms": start_ms,
    }
    before_supply = int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"])
    before_bleeding = sum(int(row.get("bleeding_ml_per_min", 0)) for row in people[YAO]["health"]["injuries"])

    begun = _ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms,
    )
    assert begun["result"] == "support_treatment_started"
    # Model a resolved defensive interruption between support frontiers.
    combat["positions"][HAN]["stance"] = "evading"
    combat["combatants"][HAN]["recovery_until_ms"] = start_ms + 11_000
    restarted = _ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms + 10_000,
    )
    assert restarted["result"] == "support_treatment_started"
    assert int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"]) == before_supply
    assert sum(int(row.get("bleeding_ml_per_min", 0)) for row in people[YAO]["health"]["injuries"]) == before_bleeding
