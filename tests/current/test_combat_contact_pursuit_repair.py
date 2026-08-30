from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import shinobi_runtime.combat.physical_defense as physical
import shinobi_runtime.martial_world.exact_combat as exact
from shinobi_runtime.combat.models import ActionProfile, CapabilityProfile
from shinobi_runtime.combat.geometry import planar_distance_mm


ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _clean_person(row: dict) -> dict:
    person = copy.deepcopy(row)
    person["fatigue_milli"] = 0
    person["health"] = {"status": "ready", "injuries": [], "blood_lost_ml": 0, "shock": 0, "consciousness": 100}
    person["poison_burdens"] = {}
    person["pending_poison_burdens"] = {}
    return person


def _people_pair():
    rows = _load("state/martial-world/people/house_tang.json")["people"]
    a = _clean_person(rows[0])
    b = _clean_person(rows[3])
    return a, b


def _jian_ledger(person_ref: str) -> dict:
    return {
        "schema": "jianghu-equipment-ledger-1.0",
        "policy_assignments": {},
        "person_loadouts": {
            person_ref: {
                "items": {"weapon_jian": 1},
                "condition_milli": {"weapon_jian": 1000},
            }
        },
    }


def test_melee_schedule_closes_the_full_600mm_dead_zone_before_contact():
    attacker, defender = _people_pair()
    people = {attacker["person_id"]: attacker, defender["person_id"]: defender}
    ledger = _jian_ledger(attacker["person_id"])
    combat = exact.initialize_combat(
        combat_ref="reach-dead-zone", side_a_refs=[attacker["person_id"]], side_b_refs=[defender["person_id"]],
        people=people, zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [defender["person_id"]]}, equipment_ledger=ledger,
    )
    combat["positions"][attacker["person_id"]].update(x_mm=0, y_mm=0)
    combat["positions"][defender["person_id"]].update(x_mm=1750, y_mm=0)
    action = exact._schedule_action(
        combat=combat, actor_ref=attacker["person_id"], target_ref=defender["person_id"],
        action_kind="thrust", weapon_ref="weapon_jian", poison_ref=None, hit_zone="chest",
        target_structure_ref=None, decision_origin="test", people=people, equipment_ledger=ledger,
    )
    assert int(action.profile.effect_parameters["approach_distance_mm"]) == 600
    moved, trace = physical.close_attacker_into_reach(
        attacker_ref=attacker["person_id"], defender_ref=defender["person_id"], positions=combat["positions"],
        attacker_position=exact._pos(combat["positions"][attacker["person_id"]]),
        defender_position=exact._pos(combat["positions"][defender["person_id"]]),
        attacker_capability=CapabilityProfile(100, 100, 100, 100, 100, 0, 0, 100),
        profile=action.profile, body_refs=list(people), obstacles=[],
    )
    assert trace["moved"] is True
    assert trace["distance_mm"] == 600
    assert planar_distance_mm(moved.to_record(), combat["positions"][defender["person_id"]]) <= physical.physical_reach_mm(action.profile)


def test_melee_approach_warning_is_not_spent_twice_as_post_close_dodge_time():
    profile = ActionProfile(
        method_ref="thrust", effect_kind="physical", delivery="direct", startup_ms=300,
        external_contact=True, speed_score=100, effect_parameters={"physical_reach_m": 1.15, "approach_time_ms": 5000},
    )
    assert physical._attack_warning_ms(profile, distance_mm=50_000) >= 5300
    assert physical._defense_displacement_window_ms(profile) == 300


def test_partial_committed_approach_moves_the_chaser_instead_of_freezing_them():
    profile = ActionProfile(
        method_ref="thrust", effect_kind="physical", delivery="direct", startup_ms=300,
        external_contact=True, speed_score=100,
        effect_parameters={"physical_reach_m": 1.0, "approach_distance_mm": 1000, "approach_time_ms": 250},
    )
    attacker = exact._pos({"zone_ref": "test", "x_mm": 0, "y_mm": 0, "elevation_mm": 0, "body_radius_mm": 300, "facing_mdeg": 0})
    defender = exact._pos({"zone_ref": "test", "x_mm": 5000, "y_mm": 0, "elevation_mm": 0, "body_radius_mm": 300, "facing_mdeg": 180000})
    positions = {"a": attacker.to_record(), "b": defender.to_record()}
    moved, trace = physical.close_attacker_into_reach(
        attacker_ref="a", defender_ref="b", positions=positions, attacker_position=attacker, defender_position=defender,
        attacker_capability=CapabilityProfile(100, 100, 100, 100, 100, 0, 0, 100),
        profile=profile, body_refs=["a", "b"], obstacles=[],
    )
    assert trace["reason"] == "partial_committed_approach"
    assert trace["distance_mm"] == 1000
    assert moved.x_mm == 1000
    assert trace["remaining_mm"] == 3000


def test_blocked_melee_approach_does_not_release_a_remote_swing(monkeypatch):
    attacker, defender = _people_pair()
    people = {attacker["person_id"]: attacker, defender["person_id"]: defender}
    ledger = _jian_ledger(attacker["person_id"])
    combat = exact.initialize_combat(
        combat_ref="blocked-remote-swing", side_a_refs=[attacker["person_id"]], side_b_refs=[defender["person_id"]],
        people=people, zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [defender["person_id"]]}, equipment_ledger=ledger,
    )
    combat["positions"][attacker["person_id"]].update(x_mm=0, y_mm=0)
    combat["positions"][defender["person_id"]].update(x_mm=5000, y_mm=0)

    original_observe = exact._observe_visible_enemies
    monkeypatch.setattr(
        exact, "_observe_visible_enemies",
        lambda combat, actor_ref, enemy_refs, people, at_ms: [] if actor_ref == defender["person_id"] else original_observe(
            combat, actor_ref=actor_ref, enemy_refs=enemy_refs, people=people, at_ms=at_ms
        ),
    )
    monkeypatch.setattr(
        exact, "close_attacker_into_reach",
        lambda **kwargs: (kwargs["attacker_position"], {"moved": False, "reason": "approach_lane_blocked", "required_mm": 3850}),
    )
    result = exact.resolve_exchange(
        combat=combat, people=people, equipment_ledger=ledger, doctrines={},
        player_ref=attacker["person_id"], player_action_kind="thrust", player_target_ref=defender["person_id"],
        player_weapon_ref="weapon_jian", player_hit_zone="chest", player_targeting_intent="lethal",
    )
    event = next(row for row in result["events"] if row.get("actor_ref") == attacker["person_id"])
    assert event["result"] == "melee_approach_blocked"
    assert "actual_ref" not in event
    assert defender["health"]["injuries"] == []


def test_committed_melee_pursuit_prevents_escape_through_the_one_second_frontier():
    withdrawer, pursuer = _people_pair()
    withdrawer["attributes"]["speed"] = 500
    withdrawer["attributes"]["dexterity"] = 500
    people = {withdrawer["person_id"]: withdrawer, pursuer["person_id"]: pursuer}
    combat = exact.initialize_combat(
        combat_ref="contested-escape", side_a_refs=[withdrawer["person_id"]], side_b_refs=[pursuer["person_id"]],
        people=people, zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": [withdrawer["person_id"]]},
    )
    combat["positions"][withdrawer["person_id"]].update(x_mm=0, y_mm=0)
    combat["positions"][pursuer["person_id"]].update(x_mm=-5000, y_mm=0)
    combat["_pending_actions"] = {
        pursuer["person_id"]: {
            "target_ref": withdrawer["person_id"], "commit_at_ms": 500, "contact_at_ms": 1500, "delivery": "direct"
        }
    }
    step = exact._disengage_step(
        combat=combat, actor_ref=withdrawer["person_id"], people=people, equipment_ledger=None, duration_ms=1000, start_ms=0
    )
    assert step["movement"]["nearest_enemy_mm"] >= 6000
    assert step["escaped"] is False
    assert step["reason"] == "retreat_contested_by_committed_melee"
    assert "escaped" not in combat["combatants"][withdrawer["person_id"]]["status_families"]


def test_hold_position_does_not_hide_an_extra_body_radius_of_melee_chase():
    attacker, _ = _people_pair()
    ledger = _jian_ledger(attacker["person_id"])
    assert exact._hold_position_weapon_for(attacker["person_id"], attacker, ledger, target_distance_mm=1150) is not None
    assert exact._hold_position_weapon_for(attacker["person_id"], attacker, ledger, target_distance_mm=1151) is None
