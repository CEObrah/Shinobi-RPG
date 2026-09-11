from __future__ import annotations

import json
from types import SimpleNamespace

from shinobi_runtime.api import transition_operations
from shinobi_runtime.api import travel_operations
from shinobi_runtime.martial_world import exact_combat


def _person(ref: str, *, side: str = "a") -> dict:
    return {
        "person_id": ref,
        "name": ref,
        "faction_ref": f"faction_{side}",
        "membership_grade": "full",
        "fatigue_milli": 0,
        "attributes": {
            "strength": 60, "speed": 60, "dexterity": 60, "endurance": 60,
            "perception": 60, "intelligence": 60, "willpower": 60,
        },
        "martial_skills": {
            "sword": 0, "spear": 0, "bow": 0, "hidden_weapons": 80,
            "unarmed": 40, "stealth_scouting": 20, "command": 20,
        },
        "qi": 20,
        "qi_control": 20,
        "current_qi_milli": 20000,
        "health": {"status": "ready", "shock": 0, "injuries": []},
    }


def test_auto_hidden_weapon_selection_respects_physical_maximum_range():
    actor = _person("actor")
    ledger = {
        "schema": "jianghu-equipment-ledger-1.0",
        "person_loadouts": {"actor": {"items": {"weapon_needle": 3}}},
    }
    close_kind, close_weapon = exact_combat._default_weapon_for(
        "actor", actor, ledger, target_distance_mm=5000
    )
    assert (close_kind, close_weapon) == ("hidden_weapon_throw", "weapon_needle")

    far_kind, far_weapon = exact_combat._default_weapon_for(
        "actor", actor, ledger, target_distance_mm=30000
    )
    assert far_kind == "unarmed_strike"
    assert far_weapon == "body_unarmed"


def test_event_record_preserves_intended_anatomical_target_even_on_early_failure():
    action = SimpleNamespace(
        actor_ref="actor", target_ref="missing", action_kind="cut", weapon_ref="weapon_jian",
        poison_ref=None, hit_zone="right_arm", target_structure_ref="right_wrist",
        decision_origin="standing_doctrine", declared_at_ms=0, start_at_ms=1, ready_delay_ms=0,
        previous_ready_weapon_ref="weapon_jian", commit_at_ms=2, release_at_ms=3,
        contact_at_ms=4, recovery_end_ms=5,
    )
    event = exact_combat._resolve_scheduled_action(
        combat={}, action=action, people={"actor": _person("actor")}, equipment_ledger={}
    )
    assert event["result"] == "invalid_target"
    assert event["hit_zone"] == "right_arm"
    assert event["target_structure_ref"] == "right_wrist"


def test_combat_narrative_summary_uses_actual_resource_and_qi_keys():
    events = [
        {
            "actor_ref": "player", "intended_ref": "enemy", "actual_ref": "enemy",
            "action_kind": "hidden_weapon_throw", "weapon_ref": "weapon_needle",
            "poison_ref": "cardiotoxic", "hit_zone": "right_arm",
            "target_structure_ref": "right_wrist", "result": "miss_no_spatial_intersection",
            "contact_at_ms": 100,
            "resource_commit": {
                "ok": True, "projectile_ref": "weapon_needle", "poison_ref": "cardiotoxic",
                "poison_dose_consumed": True,
            },
            "qi": {"current_qi_milli_spent": 125},
            "fatigue": {"added_milli": 7},
        },
        {
            "actor_ref": "player", "intended_ref": "enemy", "actual_ref": "enemy",
            "action_kind": "cut", "weapon_ref": "weapon_jian", "result": "contact",
            "contact_at_ms": 200, "hit_zone": "right_arm", "target_structure_ref": "right_wrist",
            "damage": {"wound": {"zone": "arm", "structure_ref": "forearm", "severity": 40}},
        },
    ]
    summary = transition_operations._combat_narrative_summary(events, frozenset({"enemy"}))
    assert summary["resource_summary"]["projectiles_committed"] == 1
    assert summary["resource_summary"]["poison_doses_consumed"] == 1
    assert summary["resource_summary"]["qi_milli_spent"] == 125
    assert summary["resource_summary"]["fatigue_milli_added"] == 7
    assert len(summary["material_beats"]) == 2
    assert summary["material_beats"][0]["resource_commit"]["projectile_ref"] == "weapon_needle"
    assert summary["material_beats"][1]["target_structure_ref"] == "right_wrist"
    assert summary["material_beats"][1]["wound"]["structure_ref"] == "forearm"
    assert summary["material_beats"][1]["intended_ref"] == "opposing_combatant"


def test_large_combat_keeps_complete_compact_index_and_bounded_full_sheets(monkeypatch):
    player = "p0"
    friends = [player, *[f"a{i}" for i in range(1, 12)]]
    enemies = [f"b{i}" for i in range(49)]
    refs = friends + enemies
    combatants = {
        ref: {
            "observed_refs": enemies if ref == player else [],
            "status_families": [], "ready_weapon_ref": "weapon_jian", "weapon_position": "guard",
        }
        for ref in refs
    }
    positions = {
        ref: {"x_mm": i * 350, "y_mm": (i % 7) * 500, "elevation_mm": 0, "stance": "ready"}
        for i, ref in enumerate(refs)
    }
    combat = {
        "combat_id": "combat:test", "elapsed_ms": 5000, "zone_ref": "route.test",
        "sides": {"side_a": friends, "side_b": enemies},
        "combatants": combatants, "positions": positions,
        "team_plans": {
            "side_b": {
                "plan_id": "plan:b", "primary_threat_ref": player, "tactical_problem": "multiple_threats",
                "desired_states": ["maintain_mutual_support"],
                "assignments": {
                    ref: {"role": "pressure", "target_ref": player, "preferred_action": "attack"}
                    for ref in enemies
                },
            }
        },
        "environment": {"terrain": "hills", "obstacles": [{"obstacle_ref": "dup"}]},
        "obstacles": [],
    }
    people = {ref: _person(ref, side="a" if ref in friends else "b") for ref in refs}
    monkeypatch.setattr(travel_operations, "active_combat_for_person", lambda read_json, player_id: ("combat:test", combat))
    monkeypatch.setattr(travel_operations, "combat_person_arrived", lambda combat, ref: True)

    def read_json(path: str):
        if path == "state/martial-world/equipment-ledger.json":
            return {"schema": "jianghu-equipment-ledger-1.0"}
        if path == "state/martial-world/route-operations.json":
            return {"contacts": {}}
        raise FileNotFoundError(path)

    packet = travel_operations.gm_private_combat_director_projection(
        read_json=read_json, sheet_resolver=lambda ref: people[ref], player_id=player
    )
    assert packet is not None
    assert packet["participant_count"] == 61
    assert packet["participant_index_count"] == 61
    assert len(packet["participants"]) == 61
    assert len(packet["focus_participants"]) <= 8
    assert packet["participant_projection_mode"] == "compact_large_combat_with_focal_full_sheets"
    assert all("attributes" not in row for row in packet["participants"])
    assert all("attributes" in row for row in packet["focus_participants"])
    assert "assignments" not in packet["team_plans"]["side_b"]
    assert "obstacles" not in packet["environment"]
    assert len(json.dumps(packet, sort_keys=True, separators=(",", ":"))) < 48000


def test_small_combat_retains_full_director_sheets(monkeypatch):
    player = "p0"; enemy = "b0"
    combat = {
        "combat_id": "combat:small", "sides": {"side_a": [player], "side_b": [enemy]},
        "combatants": {player: {"observed_refs": [enemy]}, enemy: {"observed_refs": [player]}},
        "positions": {
            player: {"x_mm": 0, "y_mm": 0, "elevation_mm": 0, "stance": "ready"},
            enemy: {"x_mm": 1000, "y_mm": 0, "elevation_mm": 0, "stance": "ready"},
        },
        "team_plans": {}, "obstacles": [],
    }
    people = {player: _person(player), enemy: _person(enemy, side="b")}
    monkeypatch.setattr(travel_operations, "active_combat_for_person", lambda read_json, player_id: ("combat:small", combat))
    monkeypatch.setattr(travel_operations, "combat_person_arrived", lambda combat, ref: True)
    def read_json(path: str):
        if path == "state/martial-world/equipment-ledger.json": return {"schema": "jianghu-equipment-ledger-1.0"}
        if path == "state/martial-world/route-operations.json": return {"contacts": {}}
        raise FileNotFoundError(path)
    packet = travel_operations.gm_private_combat_director_projection(
        read_json=read_json, sheet_resolver=lambda ref: people[ref], player_id=player
    )
    assert packet is not None
    assert packet["participant_projection_mode"] == "full_small_combat"
    assert len(packet["participants"]) == 2
    assert all("attributes" in row and "martial_skills" in row for row in packet["participants"])
