from __future__ import annotations

import json

from shinobi_runtime.api.travel_operations import combat_observation_scene_projection


def _reader(combat, *, route_operations=None):
    state = {
        "state/martial-world/combats.json": {
            "schema": "jianghu-combat-state-1.0",
            "combats": {"combat:test": combat} if combat is not None else {},
        }
    }
    if route_operations is not None:
        state["state/martial-world/route-operations.json"] = route_operations

    def read(path):
        if path not in state:
            raise FileNotFoundError(path)
        return state[path]

    return read


def _combat(*, player_observed, ally_observed, extra_allies=None):
    allies = ["pc", "ally", *(extra_allies or [])]
    combatants = {
        "pc": {"observed_refs": list(player_observed)},
        "ally": {"observed_refs": list(ally_observed)},
        "enemy.1": {"observed_refs": []},
        "enemy.2": {"observed_refs": []},
        "enemy.3": {"observed_refs": []},
    }
    for ref in extra_allies or []:
        combatants[ref] = {"observed_refs": ["enemy.1"]}
    return {
        "combat_id": "combat:test",
        "status": "active",
        "sides": {
            "side_a": allies,
            "side_b": ["enemy.1", "enemy.2", "enemy.3"],
        },
        "combatants": combatants,
    }


def test_player_observation_counts_only_observed_hostiles():
    result = combat_observation_scene_projection(
        read_json=_reader(
            _combat(
                player_observed=["enemy.1", "ally"],
                ally_observed=["enemy.1", "enemy.2"],
            )
        ),
        player_id="pc",
    )

    assert result is not None
    assert result["player_observation"]["observer_person_id"] == "pc"
    assert result["player_observation"]["confirmed_observed_hostile_count"] == 1
    assert result["player_observation"]["confirmed_observed_hostile_count_cumulative"] == 1
    assert result["count_semantics"] == "confirmed_observed_hostiles_not_total_force"


def test_ally_observation_remains_separate_from_player_knowledge():
    result = combat_observation_scene_projection(
        read_json=_reader(
            _combat(
                player_observed=["enemy.1"],
                ally_observed=["enemy.1", "enemy.2", "enemy.3"],
            )
        ),
        player_id="pc",
    )

    assert result is not None
    assert result["player_observation"]["confirmed_observed_hostile_count"] == 1
    assert len(result["ally_observer_summaries"]) == 1
    ally = result["ally_observer_summaries"][0]
    assert ally["observer_person_id"] == "ally"
    assert ally["confirmed_observed_hostile_count"] == 3
    assert ally["confirmed_observed_hostile_count_cumulative"] == 3
    assert result["knowledge_semantics"] == "observer_specific_not_automatically_shared"


def test_projection_never_exposes_hostile_ids_or_hidden_roster_size():
    result = combat_observation_scene_projection(
        read_json=_reader(
            _combat(
                player_observed=[],
                ally_observed=["enemy.2"],
            )
        ),
        player_id="pc",
    )

    assert result is not None
    encoded = json.dumps(result, sort_keys=True)
    assert "enemy.1" not in encoded
    assert "enemy.2" not in encoded
    assert "enemy.3" not in encoded
    assert "hostile_total" not in encoded
    assert "enemy_count" not in encoded
    assert result["ally_observer_summaries"][0]["confirmed_observed_hostile_count"] == 1


def test_friendly_observations_do_not_inflate_hostile_count():
    result = combat_observation_scene_projection(
        read_json=_reader(
            _combat(
                player_observed=["ally"],
                ally_observed=["pc", "enemy.1"],
            )
        ),
        player_id="pc",
    )

    assert result is not None
    assert result["player_observation"]["confirmed_observed_hostile_count"] == 0
    assert result["ally_observer_summaries"][0]["confirmed_observed_hostile_count"] == 1


def test_ally_projection_is_deterministically_bounded():
    extra = [f"ally.{index}" for index in range(30)]
    result = combat_observation_scene_projection(
        read_json=_reader(
            _combat(
                player_observed=["enemy.1"],
                ally_observed=["enemy.1"],
                extra_allies=extra,
            )
        ),
        player_id="pc",
        ally_limit=4,
    )

    assert result is not None
    assert [row["observer_person_id"] for row in result["ally_observer_summaries"]] == [
        "ally",
        "ally.0",
        "ally.1",
        "ally.2",
    ]


def test_zero_ally_limit_keeps_player_observation_only():
    result = combat_observation_scene_projection(
        read_json=_reader(_combat(player_observed=["enemy.1"], ally_observed=["enemy.2"])),
        player_id="pc",
        ally_limit=0,
    )

    assert result is not None
    assert result["player_observation"]["confirmed_observed_hostile_count"] == 1
    assert result["ally_observer_summaries"] == []


def test_no_active_combat_has_no_observation_projection():
    assert combat_observation_scene_projection(
        read_json=_reader(None),
        player_id="pc",
    ) is None


def test_gm_private_combat_director_can_see_hidden_scene_truth_without_changing_player_observation():
    from shinobi_runtime.api.travel_operations import gm_private_combat_director_projection

    combat = _combat(player_observed=["enemy.1"], ally_observed=["enemy.1", "enemy.2"])
    combat["positions"] = {
        "pc": {"x_mm": 0, "y_mm": 0, "facing_mdeg": 0, "stance": "braced"},
        "ally": {"x_mm": -500, "y_mm": 0, "facing_mdeg": 0, "stance": "braced"},
        "enemy.1": {"x_mm": 2000, "y_mm": 0, "facing_mdeg": 180000, "stance": "approaching"},
        "enemy.2": {"x_mm": 3500, "y_mm": 500, "facing_mdeg": 180000, "stance": "approaching"},
        "enemy.3": {"x_mm": 5000, "y_mm": -500, "facing_mdeg": 180000, "stance": "concealed"},
    }
    combat["objective"] = {"kind": "protect_cargo"}
    combat["team_plans"] = {"side_b": {"priority": "isolate_player"}}
    combat["elapsed_ms"] = 2500
    combat["zone_ref"] = "route.test"

    sheets = {
        "pc": {"name": "Wei", "fatigue_milli": 100, "health": {"status": "healthy", "injuries": []}},
        "ally": {"name": "Kai", "fatigue_milli": 200, "health": {"status": "healthy", "injuries": []}},
        "enemy.1": {"name": "Hidden One", "fatigue_milli": 300, "health": {"status": "healthy", "injuries": []}},
        "enemy.2": {"name": "Hidden Two", "fatigue_milli": 400, "health": {"status": "injured", "injuries": [{"zone": "arm", "severity": 220}]}},
        "enemy.3": {
            "name": "Hidden Three",
            "fatigue_milli": 500,
            "health": {"status": "healthy", "injuries": []},
            "hidden_goals": ["take the cargo without losing more people"],
        },
    }

    route_operations = {
        "schema": "jianghu-route-operations-state-1.0",
        "movements": {},
        "contacts": {
            "contact:test": {
                "status": "active",
                "combat_ref": "combat:test",
                "movement_ref": "movement:test",
                "route_ref": "route.test",
                "attacker_faction_ref": "faction.bandits",
                "attacker_refs": ["enemy.1", "enemy.2", "enemy.3"],
                "attacker_intent": "rob_cargo",
                "motive_kind": "loot",
                "gm_private_decision_context": {"utility_score": 120, "hostility": 15},
            }
        },
    }

    director = gm_private_combat_director_projection(
        read_json=_reader(combat, route_operations=route_operations),
        sheet_resolver=lambda ref: sheets[ref],
        player_id="pc",
    )
    public = combat_observation_scene_projection(read_json=_reader(combat), player_id="pc")

    assert director is not None
    assert director["privacy"] == "gm_private_scene_bounded_omniscient_truth_not_player_knowledge"
    assert director["participant_count"] == 5
    assert {row["person_ref"] for row in director["participants"]} >= {"enemy.1", "enemy.2", "enemy.3"}
    assert director["team_plans"]["side_b"]["priority"] == "isolate_player"
    assert director["encounter_causality"]["motive_kind"] == "loot"
    assert director["encounter_causality"]["gm_private_decision_context"]["utility_score"] == 120
    hidden_three = next(row for row in director["participants"] if row["person_ref"] == "enemy.3")
    assert hidden_three["gm_private_cognition"]["hidden_goals"] == ["take the cargo without losing more people"]
    assert director["player_observation_boundary"]["observed_hostile_person_refs"] == ["enemy.1"]
    assert public is not None
    assert public["player_observation"]["confirmed_observed_hostile_count"] == 1
    assert "enemy.3" not in json.dumps(public, sort_keys=True)
