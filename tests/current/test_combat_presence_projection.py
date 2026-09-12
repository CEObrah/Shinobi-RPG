from __future__ import annotations

import copy

from shinobi_runtime.api.travel_operations import combat_observation_scene_projection
from shinobi_runtime.martial_world.physical_presence import (
    effective_person_presence,
    physical_unavailable_person_refs,
    same_effective_location,
)


ROUTES = "state/martial-world/route-operations.json"
COMBATS = "state/martial-world/combats.json"


def _reader(records):
    def read_json(path):
        if path not in records:
            raise FileNotFoundError(path)
        return copy.deepcopy(records[path])

    return read_json


def _combat(*, elapsed_ms: int):
    return {
        "combat_id": "combat.road",
        "status": "active",
        "elapsed_ms": elapsed_ms,
        "zone_ref": "route.changan.huashan",
        "sides": {
            "side_a": ["pc", "ally.arrived", "ally.reinforcing"],
            "side_b": ["enemy.1"],
        },
        "combatants": {
            "pc": {
                "status_families": [],
                "observed_refs": ["enemy.1"],
            },
            "ally.arrived": {
                "status_families": [],
                "observed_refs": ["enemy.1"],
            },
            "ally.reinforcing": {
                "status_families": ["reinforcing"],
                "reinforcement_at_ms": 8000,
                "observed_refs": [],
            },
            "enemy.1": {
                "status_families": [],
                "observed_refs": ["pc"],
            },
        },
    }


def _records(*, elapsed_ms: int):
    return {
        COMBATS: {
            "schema": "jianghu-combat-state-1.0",
            "combats": {"combat.road": _combat(elapsed_ms=elapsed_ms)},
        },
        ROUTES: {
            "movements": {
                "move.reserve": {
                    "status": "traveling",
                    "route_ref": "route.changan.huashan",
                    "participant_refs": ["ally.reinforcing"],
                }
            }
        },
    }


def test_future_reinforcement_keeps_prior_physical_owner_until_arrival():
    read_json = _reader(_records(elapsed_ms=5000))
    pc = {"person_id": "pc", "location_ref": "site.origin"}
    reserve = {"person_id": "ally.reinforcing", "location_ref": "site.origin"}

    player_presence = effective_person_presence(read_json, "pc", person=pc)
    reserve_presence = effective_person_presence(
        read_json, "ally.reinforcing", person=reserve
    )

    assert player_presence["presence_kind"] == "combat"
    assert reserve_presence["presence_kind"] == "route"
    assert reserve_presence["owner_ref"] == "move.reserve"
    assert same_effective_location(
        read_json,
        "pc",
        "ally.reinforcing",
        left_person=pc,
        right_person=reserve,
    ) is False
    # Registration still reserves the reinforcement from unrelated activities;
    # only its physical location stays outside the combat until arrival.
    assert "ally.reinforcing" in physical_unavailable_person_refs(read_json)


def test_reinforcement_enters_exact_combat_presence_at_registered_clock():
    read_json = _reader(_records(elapsed_ms=8000))
    pc = {"person_id": "pc", "location_ref": "site.origin"}
    reserve = {"person_id": "ally.reinforcing", "location_ref": "site.origin"}

    reserve_presence = effective_person_presence(
        read_json, "ally.reinforcing", person=reserve
    )

    assert reserve_presence["presence_kind"] == "combat"
    assert reserve_presence["owner_ref"] == "combat.road"
    assert same_effective_location(
        read_json,
        "pc",
        "ally.reinforcing",
        left_person=pc,
        right_person=reserve,
    ) is True


def test_combat_scene_projection_exposes_only_arrived_friendly_cast():
    before = combat_observation_scene_projection(
        read_json=_reader(_records(elapsed_ms=5000)),
        player_id="pc",
    )
    after = combat_observation_scene_projection(
        read_json=_reader(_records(elapsed_ms=8000)),
        player_id="pc",
    )

    assert before is not None and after is not None
    assert before["friendly_participant_person_ids"] == ["pc", "ally.arrived"]
    assert before["friendly_participant_count"] == 2
    assert [
        row["observer_person_id"] for row in before["ally_observer_summaries"]
    ] == ["ally.arrived"]

    assert after["friendly_participant_person_ids"] == [
        "pc",
        "ally.arrived",
        "ally.reinforcing",
    ]
    assert after["friendly_participant_count"] == 3
    assert after["friendly_presence_semantics"] == (
        "arrived_exact_combat_participants_only"
    )


def test_invalid_reinforcement_clock_fails_closed_as_not_arrived():
    records = _records(elapsed_ms=9000)
    state = records[COMBATS]["combats"]["combat.road"]["combatants"][
        "ally.reinforcing"
    ]
    state["reinforcement_at_ms"] = "soon"
    read_json = _reader(records)
    reserve = {"person_id": "ally.reinforcing", "location_ref": "site.origin"}

    presence = effective_person_presence(
        read_json, "ally.reinforcing", person=reserve
    )

    assert presence["presence_kind"] == "route"
