from __future__ import annotations

import json

from shinobi_runtime.api.travel_operations import combat_observation_scene_projection
from shinobi_runtime.martial_world.exact_combat import initialize_combat


def _reader(combat):
    payload = {
        "state/martial-world/combats.json": {
            "schema": "jianghu-combat-state-1.0",
            "combats": {"combat:test": combat},
        }
    }

    def read(path):
        if path not in payload:
            raise FileNotFoundError(path)
        return payload[path]

    return read


def test_mutual_awareness_does_not_observe_future_enemy_reinforcements():
    people = {
        "pc": {"person_id": "pc", "health": {"status": "ready", "injuries": [], "consciousness": 100}},
        "enemy.arrived": {"person_id": "enemy.arrived", "health": {"status": "ready", "injuries": [], "consciousness": 100}},
        "enemy.future": {"person_id": "enemy.future", "health": {"status": "ready", "injuries": [], "consciousness": 100}},
    }
    combat = initialize_combat(
        combat_ref="combat:test",
        side_a_refs=["pc"],
        side_b_refs=["enemy.arrived", "enemy.future"],
        people=people,
        zone_ref="test",
        started_at="0061-10-19T21:15:00",
        objective={"kind": "eliminate", "target_refs": ["enemy.arrived"]},
        awareness_mode="mutual",
        reinforcement_delays_ms={"enemy.future": 5000},
    )
    pc_state = combat["combatants"]["pc"]
    assert pc_state["observed_refs"] == ["enemy.arrived"]
    assert set(pc_state["observed_status_families"]) == {"enemy.arrived"}


def test_projection_separates_active_allies_from_casualty_bodies_and_aggregates_enemy_casualties():
    combat = {
        "combat_id": "combat:test",
        "status": "active",
        "elapsed_ms": 5000,
        "sides": {
            "side_a": ["pc", "ally.active", "ally.down", "ally.dead"],
            "side_b": ["enemy.1", "enemy.2", "enemy.3", "enemy.4", "enemy.5"],
        },
        "combatants": {
            "pc": {
                "status_families": [],
                "observed_refs": ["enemy.1", "enemy.2", "enemy.3", "enemy.4", "enemy.5"],
                "observed_status_families": {
                    "enemy.1": [],
                    "enemy.2": ["wounded"],
                    "enemy.3": ["incapacitated"],
                    "enemy.4": ["dead"],
                },
            },
            "ally.active": {"status_families": [], "observed_refs": []},
            "ally.down": {"status_families": ["incapacitated"], "observed_refs": []},
            "ally.dead": {"status_families": ["dead"], "observed_refs": []},
            "enemy.1": {"status_families": []},
            "enemy.2": {"status_families": ["wounded"]},
            "enemy.3": {"status_families": ["incapacitated"]},
            "enemy.4": {"status_families": ["dead"]},
            "enemy.5": {"status_families": []},
        },
    }
    result = combat_observation_scene_projection(read_json=_reader(combat), player_id="pc")
    assert result is not None
    assert result["friendly_participant_person_ids"] == ["pc", "ally.active"]
    assert result["friendly_body_person_ids"] == ["pc", "ally.active", "ally.down", "ally.dead"]
    assert result["friendly_incapacitated_person_ids"] == ["ally.down"]
    assert result["friendly_dead_person_ids"] == ["ally.dead"]
    assert result["player_observation"]["confirmed_observed_hostile_count"] == 5
    assert result["player_hostile_status_observation"] == {
        "observer_person_id": "pc",
        "last_observed_active_unwounded_count": 1,
        "last_observed_active_wounded_count": 1,
        "last_observed_incapacitated_count": 1,
        "last_observed_dead_count": 1,
        "observed_status_unknown_count": 1,
        "status_semantics": "last_direct_observation_not_omniscient_current_state",
    }
    encoded = json.dumps(result, sort_keys=True)
    assert "enemy.1" not in encoded
    assert "enemy.5" not in encoded


def test_confirmed_observed_count_is_explicitly_not_current_active_strength():
    combat = {
        "combat_id": "combat:test",
        "status": "active",
        "elapsed_ms": 1,
        "sides": {"side_a": ["pc"], "side_b": ["enemy.1"]},
        "combatants": {
            "pc": {
                "status_families": [],
                "observed_refs": ["enemy.1"],
                "observed_status_families": {"enemy.1": ["dead"]},
            },
            "enemy.1": {"status_families": ["dead"]},
        },
    }
    result = combat_observation_scene_projection(read_json=_reader(combat), player_id="pc")
    assert result is not None
    assert result["player_observation"]["confirmed_observed_hostile_count"] == 1
    assert result["player_hostile_status_observation"]["last_observed_dead_count"] == 1
    assert result["count_semantics"] == "confirmed_observed_hostiles_ever_detected_not_current_active_or_total_force"
