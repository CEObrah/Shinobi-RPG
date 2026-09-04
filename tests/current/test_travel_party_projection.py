from __future__ import annotations

from shinobi_runtime.api.travel_operations import (
    movement_scene_projection,
    public_site_scene_projection,
)


def _reader(state):
    def read(path):
        if path not in state:
            raise FileNotFoundError(path)
        return state[path]
    return read


def test_player_route_movement_projects_exact_cotravelers_and_progress():
    route_state = {
        "state/martial-world/route-operations.json": {
            "movements": {
                "escort_muster:test": {
                    "movement_kind": "escort_muster",
                    "status": "active",
                    "route_ref": "route.luoyang.changan",
                    "source_place_ref": "luoyang",
                    "destination_place_ref": "huashan",
                    "participant_refs": ["pc", "ally", "ally"],
                    "elapsed_seconds": 500,
                    "required_seconds": 1000,
                },
                "other:test": {
                    "movement_kind": "travel",
                    "status": "active",
                    "route_ref": "route.luoyang.changan",
                    "participant_refs": ["stranger"],
                },
            }
        }
    }
    sheets = {
        "pc": {"person_id": "pc", "location_ref": "site.home"},
        "ally": {"person_id": "ally", "location_ref": "site.home"},
        "stranger": {"person_id": "stranger", "location_ref": "site.home"},
    }

    result = movement_scene_projection(
        read_json=_reader(route_state),
        sheet_resolver=lambda ref: sheets[ref],
        player_id="pc",
        player_sheet=sheets["pc"],
    )

    assert result is not None
    assert result["movement_ref"] == "escort_muster:test"
    assert result["participant_person_ids"] == ["pc", "ally"]
    assert result["participant_count"] == 2
    assert result["progress_milli"] == 500
    assert "stranger" not in result["participant_person_ids"]


def test_route_projection_is_absent_when_player_has_no_active_movement():
    state = {"state/martial-world/route-operations.json": {"movements": {}}}
    player = {"person_id": "pc", "location_ref": "site.home"}
    assert movement_scene_projection(
        read_json=_reader(state),
        sheet_resolver=lambda _ref: player,
        player_id="pc",
        player_sheet=player,
    ) is None


def test_public_site_projection_exposes_exact_count_and_diverse_bounded_sample():
    scene = {
        "location_id": "site.changan.inn",
        "derived_present_person_ids": [
            "mw.person.jade_gate_escorts.0001",
            "mw.person.jade_gate_escorts.0002",
            "mw.person.black_lance_company.0001",
            "mw.person.black_lance_company.0002",
            "mw.person.seven_star_sect.0001",
            "civic.changan.merchant_elite",
        ],
    }

    result = public_site_scene_projection(scene, sample_limit=4)

    assert result is not None
    assert result["site_ref"] == "site.changan.inn"
    assert result["derived_attendee_count"] == 6
    assert result["presence_semantics"] == "shared_public_site_only"
    assert result["sample_person_ids"] == [
        "mw.person.jade_gate_escorts.0001",
        "mw.person.black_lance_company.0001",
        "mw.person.seven_star_sect.0001",
        "civic.changan.merchant_elite",
    ]


def test_public_site_projection_zero_sample_keeps_scale_without_person_reads():
    scene = {
        "location_id": "site.changan.inn",
        "derived_present_person_ids": ["person.a.1", "person.b.1"],
    }
    result = public_site_scene_projection(scene, sample_limit=0)
    assert result is not None
    assert result["derived_attendee_count"] == 2
    assert result["sample_person_ids"] == []


def test_public_site_projection_deduplicates_attendees_and_is_absent_on_route():
    site_scene = {
        "location_id": "site.changan.inn",
        "derived_present_person_ids": ["person.a.1", "person.a.1", "person.b.1"],
    }
    site_result = public_site_scene_projection(site_scene, sample_limit=8)
    assert site_result is not None
    assert site_result["derived_attendee_count"] == 2
    assert site_result["sample_person_ids"] == ["person.a.1", "person.b.1"]

    route_scene = {
        "location_id": "route.changan.huashan",
        "derived_present_person_ids": ["person.a.1"],
    }
    assert public_site_scene_projection(route_scene) is None
