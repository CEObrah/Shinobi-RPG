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
