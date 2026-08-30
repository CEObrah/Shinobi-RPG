from __future__ import annotations

import copy
from pathlib import Path

import shinobi_runtime.commands.jianghu_extended as extended
from shinobi_runtime.martial_world.combat_exertion import fatigue_performance_milli
from shinobi_runtime.martial_world.doctrines import resolve_individual_doctrine
from shinobi_runtime.martial_world.exact_combat import capability_from_person, initialize_combat, resolve_exchange

ROOT = Path(__file__).resolve().parents[2]


def _person(*, sword: int, strength: int, speed: int, dexterity: int, perception: int, fatigue: int) -> dict:
    return {
        "attributes": {
            "strength": strength, "speed": speed, "dexterity": dexterity,
            "endurance": 84, "perception": perception, "willpower": 70,
        },
        "martial_skills": {"sword": sword},
        "health": {"status": "ready", "injuries": []},
        "fatigue_milli": fatigue,
        "body_mass_kg": 64,
    }


def _combat_person(
    person_id: str, *, faction_ref: str, sword: int, strength: int, speed: int,
    dexterity: int, endurance: int, perception: int, willpower: int, fatigue: int,
) -> dict:
    return {
        "person_id": person_id,
        "faction_ref": faction_ref,
        "body_mass_kg": 64,
        "attributes": {
            "strength": strength, "speed": speed, "dexterity": dexterity,
            "endurance": endurance, "perception": perception,
            "intelligence": 60, "willpower": willpower,
        },
        "martial_skills": {
            "sword": sword, "spear": 0, "bow": 0, "hidden_weapons": 0,
            "unarmed": 10, "stealth_scouting": 0, "command": 0,
        },
        "qi": 0,
        "qi_control": 0,
        "current_qi_milli": 0,
        "fatigue_milli": fatigue,
        "health": {
            "status": "ready", "injuries": [], "blood_lost_ml": 0,
            "shock": 0, "consciousness": 100,
        },
        "poison_burdens": {},
        "pending_poison_burdens": {},
    }


def test_severe_fatigue_preserves_relative_combat_capability():
    assert fatigue_performance_milli(0) == 1000
    assert fatigue_performance_milli(3000) == 250
    assert fatigue_performance_milli(9000) == 250

    wei = capability_from_person(
        _person(sword=115, strength=82, speed=90, dexterity=94, perception=96, fatigue=9000),
        action_skill="sword",
    )
    ordinary = capability_from_person(
        _person(sword=45, strength=58, speed=58, dexterity=58, perception=58, fatigue=9000),
        action_skill="sword",
    )
    assert wei.offense > ordinary.offense > 0
    assert wei.control > ordinary.control > 0
    assert wei.reaction > ordinary.reaction > 0
    assert wei.mobility > ordinary.mobility > 0


def test_severely_fatigued_elite_lethal_sword_attack_still_converts_exposed_target():
    wei = _combat_person(
        "wei", faction_ref="house_tang", sword=115, strength=82, speed=90,
        dexterity=94, endurance=84, perception=96, willpower=92, fatigue=9000,
    )
    target = _combat_person(
        "target", faction_ref="test_enemy", sword=1, strength=25, speed=20,
        dexterity=20, endurance=40, perception=1, willpower=30, fatigue=9000,
    )
    people = {"wei": wei, "target": target}
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {
            "wei": {
                "items": {"weapon_jian": 1},
                "condition_milli": {"weapon_jian": 1000},
            },
            "target": {"items": {}, "condition_milli": {}},
        },
    }
    combat = initialize_combat(
        combat_ref="combat.test.exhausted-elite-conversion",
        side_a_refs=["wei"], side_b_refs=["target"], people=people,
        zone_ref="site.test", started_at="0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": ["target"]},
        awareness_mode="mutual", initial_range_band=1,
        equipment_ledger=ledger, initial_ready_weapons={"wei": "weapon_jian"},
    )
    combat["positions"]["wei"].update(x_mm=0, y_mm=0, elevation_mm=0, facing_mdeg=0)
    combat["positions"]["target"].update(x_mm=900, y_mm=0, elevation_mm=0, facing_mdeg=180000)
    # Make the comparison about offensive conversion, not an artificial mutual
    # duel. The target is exposed and has not established a useful defense read.
    combat["combatants"]["target"]["observed_refs"] = []
    combat["combatants"]["target"]["awareness_confidence_milli"] = 0
    combat["combatants"]["target"]["surprise_milli"] = 1000

    cursor_combat = combat
    cursor_people = people
    cursor_ledger = ledger
    player_events = []
    for _ in range(8):
        if cursor_combat.get("status") != "active":
            break
        result = resolve_exchange(
            combat=cursor_combat, people=cursor_people,
            equipment_ledger=cursor_ledger, doctrines={}, player_ref="wei",
            player_action_kind="thrust", player_target_ref="target",
            player_weapon_ref="weapon_jian", player_hit_zone="neck",
            player_target_structure_ref="throat", player_targeting_intent="lethal",
        )
        player_events.extend(
            event for event in result["events"]
            if event.get("actor_ref") == "wei"
        )
        cursor_combat = result["combat_after"]
        cursor_people = result["people_after"]
        cursor_ledger = result["equipment_ledger_after"]

    target_after = cursor_people["target"]
    injuries = target_after.get("health", {}).get("injuries", [])
    assert player_events
    assert injuries or target_after.get("health", {}).get("status") in {"injured", "incapacitated", "dead"}
    assert any(
        event.get("result") not in {
            "defended_or_missed", "miss_no_spatial_intersection",
            "target_unavailable", "action_rejected",
        }
        for event in player_events
    )


def test_lethal_pursuit_template_changes_engagement_only():
    base = resolve_individual_doctrine("doctrine.tang_wei.precision_function_denial")
    pursuit = resolve_individual_doctrine("doctrine.tang_wei.precision_function_denial.lethal_pursuit")
    assert pursuit["engagement"]["initiative_posture"] == "assertive"
    assert pursuit["engagement"]["commitment_posture"] == "committed"
    assert pursuit["engagement"]["pursuit_posture"] == "persistent"
    assert pursuit["engagement"]["movement_economy"] == "mobile"
    assert pursuit["resource_discipline"] == base["resource_discipline"]
    assert pursuit["force_policy"] == base["force_policy"]
    assert pursuit["targeting"] == base["targeting"]


def test_explicit_lethal_until_resolution_uses_temporary_pursuit_doctrine(monkeypatch):
    seen: list[str | None] = []

    monkeypatch.setattr(extended, "hydrate_equipment_ledger", lambda value: copy.deepcopy(value))
    monkeypatch.setattr(extended, "compact_equipment_ledger", lambda value: copy.deepcopy(value))
    monkeypatch.setattr(extended, "apply_martial_events", lambda state, events, side_by_ref: copy.deepcopy(state))

    def fake_default_target_for(*, people, actor_ref, **kwargs):
        seen.append(people[actor_ref].get("combat_doctrine_ref"))
        return "enemy"

    def fake_default_action_for(**kwargs):
        return "cut", "weapon_jian"

    def fake_resolve_exchange(**kwargs):
        people = copy.deepcopy(kwargs["people"])
        seen.append(people[kwargs["player_ref"]].get("combat_doctrine_ref"))
        combat = copy.deepcopy(kwargs["combat"])
        combat["status"] = "resolved"
        combat["elapsed_ms"] = int(combat.get("elapsed_ms", 0)) + 1000
        return {
            "combat_after": combat,
            "people_after": people,
            "equipment_ledger_after": copy.deepcopy(kwargs["equipment_ledger"]),
            "events": [],
        }

    monkeypatch.setattr(extended, "default_target_for", fake_default_target_for)
    monkeypatch.setattr(extended, "default_action_for", fake_default_action_for)
    monkeypatch.setattr(extended, "resolve_exchange", fake_resolve_exchange)

    original = "doctrine.tang_wei.precision_function_denial"
    result = extended._resolve_player_combat_span(
        combat={"status": "active", "elapsed_ms": 0, "sides": {"a": ["pc"], "b": ["enemy"]}},
        people={
            "pc": {"combat_doctrine_ref": original, "health": {"status": "ready"}},
            "enemy": {"faction_ref": "enemy", "health": {"status": "ready"}},
        },
        equipment_ledger={}, doctrines={}, player_ref="pc", social_state={}, player_retinue_context=None,
        raw_target_ref="auto", raw_action_kind="attack", raw_weapon_ref="auto", hit_zone="auto",
        target_structure_ref=None, targeting_intent="lethal", explicit_poison_ref=None, poison_auto=False,
        explicit_qi_allocation_milli=None, qi_auto=False, exchange_count=None, duration_seconds=None,
        until_resolution=True, frontier_exchanges=4,
    )

    assert seen == [
        "doctrine.tang_wei.precision_function_denial.lethal_pursuit",
        "doctrine.tang_wei.precision_function_denial.lethal_pursuit",
    ]
    assert result["people_after"]["pc"]["combat_doctrine_ref"] == original


def test_combat_narration_requires_complete_paginated_receipt_before_absence_claims():
    text = (ROOT / "plugins/shinobi-rpg/skill/shinobi-game-master/references/combat.md").read_text(encoding="utf-8")
    assert "follow `next_object_ref` sequentially from the first page until it is null" in text
    assert "Never sample arbitrary event offsets and infer an absence" in text
    assert "Kill as many as possible as quickly as possible" in text
