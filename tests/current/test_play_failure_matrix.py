from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import shinobi_runtime.martial_world.exact_combat as exact
from shinobi_runtime.martial_world.equipment_state import effective_person_loadout
from shinobi_runtime.martial_world.property import provenance_claim
from play_failure_fixture import ENEMY, HAN, PLAYER, YAO, played_medic_fixture, stage_treatment_fixture

ROOT = Path(__file__).resolve().parents[2]

def test_played_attack_plus_medic_order_is_one_semantic_combat_action():
    combat, people, ledger = played_medic_fixture()

    result = exact.resolve_exchange(
        combat=combat,
        people=people,
        equipment_ledger=ledger,
        doctrines={},
        player_ref=PLAYER,
        player_action_kind="unarmed_strike",
        player_target_ref=ENEMY,
        player_weapon_ref="body_unarmed",
        player_hit_zone="chest",
        player_targeting_intent="disable",
        player_ally_orders=[{"actor_ref": HAN, "task": "treat", "target_ref": YAO}],
        player_retinue_context={"member_refs": [HAN, YAO]},
    )

    player_events = [row for row in result["events"] if row.get("actor_ref") == PLAYER]
    support_events = [
        row for row in result["events"]
        if row.get("actor_ref") == HAN and row.get("action_kind") == "ally_support"
    ]
    assert player_events, "Wei-equivalent personal combat disappeared"
    assert support_events, "simultaneous medic order disappeared"
    assert all(row.get("decision_origin") == "player_ally_order" for row in support_events)
    assert all(row.get("intended_ref") == YAO for row in support_events)

    info = result.get("combat_information")
    assert isinstance(info, Mapping)
    assert info.get("scale") == "exact_people"
    assert int(info.get("observed_hostiles_cumulative", 0)) >= int(info.get("observed_active_engaged", 0))

    after = result["combat_after"]
    support = after["combatants"][HAN]["support_task"]
    assert support["task"] == "treat"
    assert support["target_ref"] == YAO
    assert support["status"] == "active"
    assert support["issued_by_ref"] == PLAYER
    assert after["positions"][YAO]["stance"] == "fallen"
    assert int(after["positions"][YAO]["body_radius_mm"]) <= 140


def test_standing_field_medic_profession_is_not_a_passive_combat_posture():
    source = (ROOT / "runtime/shinobi_runtime/combat/team_tactics.py").read_text()
    exact_source = (ROOT / "runtime/shinobi_runtime/martial_world/exact_combat.py").read_text()
    assert "medical_support_hold" not in source
    assert "holding_medical_support_position" not in exact_source
    assert "'field_medic': 'pressure'" in source


def stage_treatment_fixture() -> tuple[dict, dict, dict, int]:
    combat, people, ledger = played_medic_fixture()
    combat["positions"][HAN]["x_mm"] = int(combat["positions"][YAO]["x_mm"]) - 500
    combat["positions"][HAN]["y_mm"] = int(combat["positions"][YAO]["y_mm"])
    start_ms = int(combat.get("elapsed_ms", 0))
    combat["combatants"][HAN]["support_task"] = {
        "task": "treat",
        "target_ref": YAO,
        "status": "active",
        "issued_by_ref": PLAYER,
        "issued_at_ms": start_ms,
    }
    return combat, people, ledger, start_ms


def test_field_medic_can_complete_conserved_emergency_stabilization():
    combat, people, ledger, start_ms = stage_treatment_fixture()
    before_bleeding = int(people[YAO]["health"]["injuries"][0]["bleeding_ml_per_min"])
    before_supply = int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"])

    begun = exact._ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms,
    )
    assert begun["result"] == "support_treatment_started"

    completed = exact._ally_support_step(
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
    combat, people, ledger, start_ms = stage_treatment_fixture()
    ledger["person_loadouts"][HAN]["items"].pop("tool_physicians_kit", None)
    before_supply = int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"])
    before_bleeding = sum(int(row.get("bleeding_ml_per_min", 0)) for row in people[YAO]["health"]["injuries"])

    begun = exact._ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms,
    )
    assert begun["result"] == "support_treatment_started"
    blocked = exact._ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms + 10_000,
    )
    assert blocked["result"] == "support_treatment_no_physician_kit"
    assert combat["combatants"][HAN]["support_task"]["status"] == "blocked"
    assert combat["combatants"][HAN]["support_task"]["blocked_reason"] == "physician_kit_unavailable"
    assert int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"]) == before_supply
    assert sum(int(row.get("bleeding_ml_per_min", 0)) for row in people[YAO]["health"]["injuries"]) == before_bleeding


def test_field_treatment_clock_restarts_after_combat_interruption():
    combat, people, ledger, start_ms = stage_treatment_fixture()
    before_supply = int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"])
    before_bleeding = sum(int(row.get("bleeding_ml_per_min", 0)) for row in people[YAO]["health"]["injuries"])

    begun = exact._ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms,
    )
    assert begun["result"] == "support_treatment_started"
    combat["positions"][HAN]["stance"] = "evading"
    combat["combatants"][HAN]["recovery_until_ms"] = start_ms + 11_000
    restarted = exact._ally_support_step(
        combat=combat, actor_ref=HAN, target_ref=YAO, task="treat",
        people=people, equipment_ledger=ledger, start_ms=start_ms + 10_000,
    )
    assert restarted["result"] == "support_treatment_started"
    assert int(effective_person_loadout(ledger, HAN)["items"]["supply_medical_bundle"]) == before_supply
    assert sum(int(row.get("bleeding_ml_per_min", 0)) for row in people[YAO]["health"]["injuries"]) == before_bleeding
