from __future__ import annotations

import json

from shinobi_runtime.api.travel_operations import combat_observation_scene_projection


def _reader(combat):
    state = {
        "state/martial-world/combats.json": {
            "schema": "jianghu-combat-state-1.0",
            "combats": {"combat:test": combat} if combat is not None else {},
        }
    }

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
    assert result["player_observation"] == {
        "observer_person_id": "pc",
        "confirmed_observed_hostile_count": 1,
    }
    assert result["count_semantics"] == "confirmed_observed_hostiles_ever_detected_not_current_active_or_total_force"


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
    assert result["ally_observer_summaries"] == [
        {"observer_person_id": "ally", "confirmed_observed_hostile_count": 3}
    ]
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
