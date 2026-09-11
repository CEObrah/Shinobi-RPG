import copy

from shinobi_runtime.martial_world.physical_presence import (
    effective_person_presence,
    physical_unavailable_person_refs,
    same_effective_location,
)


ROUTES = "state/martial-world/route-operations.json"
CUSTODY = "state/martial-world/custody.json"
COMBATS = "state/martial-world/combats.json"


def _reader(records):
    def read_json(path):
        if path not in records:
            raise FileNotFoundError(path)
        return copy.deepcopy(records[path])
    return read_json


def test_route_owner_overrides_stored_endpoint_without_using_scene_projection():
    records = {
        ROUTES: {
            "movements": {
                "move.wei": {
                    "status": "traveling",
                    "route_ref": "route.luoyang.huashan",
                    "participant_refs": ["pc_wei_tang", "guard.one"],
                }
            }
        }
    }
    read_json = _reader(records)
    wei = {"person_id": "pc_wei_tang", "location_ref": "site.house_tang"}

    presence = effective_person_presence(read_json, "pc_wei_tang", person=wei)

    assert presence["presence_kind"] == "route"
    assert presence["owner_ref"] == "move.wei"
    assert presence["location_ref"] == "route.luoyang.huashan"
    assert presence["space_ref"] == "movement:move.wei"
    assert presence["available_for_site_activity"] is False
    assert "pc_wei_tang" in physical_unavailable_person_refs(read_json)


def test_two_independent_parties_on_same_road_are_not_mechanically_colocated():
    records = {
        ROUTES: {
            "movements": {
                "move.wei": {
                    "status": "traveling",
                    "route_ref": "route.luoyang.huashan",
                    "participant_refs": ["pc_wei_tang", "guard.one"],
                },
                "move.other": {
                    "status": "traveling",
                    "route_ref": "route.luoyang.huashan",
                    "participant_refs": ["traveler.other"],
                },
            }
        }
    }
    read_json = _reader(records)
    wei = {"person_id": "pc_wei_tang", "location_ref": "site.house_tang"}
    guard = {"person_id": "guard.one", "location_ref": "site.house_tang"}
    other = {"person_id": "traveler.other", "location_ref": "site.huashan"}

    assert same_effective_location(
        read_json, "pc_wei_tang", "guard.one", left_person=wei, right_person=guard
    ) is True
    assert same_effective_location(
        read_json, "pc_wei_tang", "traveler.other", left_person=wei, right_person=other
    ) is False
    assert effective_person_presence(read_json, "pc_wei_tang", person=wei)["location_ref"] == \
        effective_person_presence(read_json, "traveler.other", person=other)["location_ref"]


def test_custody_and_exact_combat_take_precedence_over_route_or_stored_location():
    records = {
        ROUTES: {
            "movements": {
                "move.wei": {
                    "status": "traveling",
                    "route_ref": "route.luoyang.huashan",
                    "participant_refs": ["pc_wei_tang"],
                }
            }
        },
        COMBATS: {
            "combats": {
                "combat.road": {
                    "status": "active",
                    "zone_ref": "zone.road.cut",
                    "combatants": {"pc_wei_tang": {}},
                }
            }
        },
        CUSTODY: {
            "records": [
                {
                    "custody_id": "custody.wei",
                    "person_ref": "pc_wei_tang",
                    "status": "detained",
                    "location_ref": "site.enemy_stockade",
                }
            ]
        },
    }
    read_json = _reader(records)
    wei = {"person_id": "pc_wei_tang", "location_ref": "site.house_tang"}

    presence = effective_person_presence(read_json, "pc_wei_tang", person=wei)

    assert presence["presence_kind"] == "custody"
    assert presence["space_ref"] == "site:site.enemy_stockade"
    assert presence["location_ref"] == "site.enemy_stockade"


def test_moving_captive_uses_carrier_movement_geometry_without_losing_custody():
    records = {
        ROUTES: {
            "movements": {
                "move.captors": {
                    "status": "traveling",
                    "route_ref": "route.luoyang.huashan",
                    "participant_refs": ["captor.one"],
                    "captive_refs": ["captive.one"],
                }
            }
        },
        CUSTODY: {
            "records": [
                {
                    "custody_id": "custody.captive.one",
                    "person_ref": "captive.one",
                    "status": "restrained",
                    "location_ref": "move.captors",
                }
            ]
        },
    }
    read_json = _reader(records)
    captor = {"person_id": "captor.one", "location_ref": "site.origin"}
    captive = {"person_id": "captive.one", "location_ref": "site.old_home"}

    presence = effective_person_presence(read_json, "captive.one", person=captive)

    assert presence["presence_kind"] == "custody"
    assert presence["owner_ref"] == "custody.captive.one"
    assert presence["physical_owner_ref"] == "move.captors"
    assert presence["location_ref"] == "route.luoyang.huashan"
    assert presence["space_ref"] == "movement:move.captors"
    assert presence["available_for_site_activity"] is False
    assert same_effective_location(
        read_json, "captor.one", "captive.one", left_person=captor, right_person=captive
    ) is True


def test_malformed_custody_owner_fails_closed_instead_of_freeing_person():
    read_json = _reader({CUSTODY: {"records": {"bad": "shape"}}})
    person = {"person_id": "captive.one", "location_ref": "site.old_home"}
    import pytest
    with pytest.raises(ValueError, match="custody records are malformed"):
        effective_person_presence(read_json, "captive.one", person=person)


def test_malformed_combat_owner_fails_closed_instead_of_restoring_stale_location():
    read_json = _reader({COMBATS: {"combats": []}})
    person = {"person_id": "fighter.one", "location_ref": "site.old_home"}
    import pytest
    with pytest.raises(ValueError, match="combat registry is malformed"):
        effective_person_presence(read_json, "fighter.one", person=person)


def test_malformed_route_owner_fails_closed_instead_of_teleporting_home():
    read_json = _reader({ROUTES: {"movements": []}})
    person = {"person_id": "traveler.one", "location_ref": "site.old_home"}
    import pytest
    with pytest.raises(ValueError, match="route movement registry is malformed"):
        effective_person_presence(read_json, "traveler.one", person=person)
