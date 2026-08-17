from __future__ import annotations

from types import SimpleNamespace

from shinobi_runtime.commands import player_mission_continuity as continuity
from shinobi_runtime.commands.player_mission_continuity import mission_assignment_signature


def _brief(**changes):
    value = {
        "subject_kind": "person",
        "subject_ref": "support.daimyo.noboru_shimizu",
        "origin_place_ref": "place.konoha",
        "destination_place_ref": "place.fire.capital",
        "route_id": "route_fire_capital_konoha",
        "threat_source_ref": None,
    }
    value.update(changes)
    return value


def test_protect_and_escort_same_person_route_are_one_assignment_family() -> None:
    assert mission_assignment_signature("protect", _brief()) == mission_assignment_signature(
        "escort", _brief()
    )


def test_person_transit_signature_distinguishes_route_and_subject() -> None:
    baseline = mission_assignment_signature("escort", _brief())
    assert baseline != mission_assignment_signature(
        "escort", _brief(route_id="route_konoha_fire_west", destination_place_ref="place.fire.western.border")
    )
    assert baseline != mission_assignment_signature(
        "escort", _brief(subject_ref="support.daimyo.someone_else")
    )


def test_non_transit_objectives_remain_distinct() -> None:
    investigation = _brief(
        subject_kind="place",
        subject_ref="place.fire.western.border",
        destination_place_ref="place.fire.western.border",
        route_id="route_konoha_fire_west",
    )
    assert mission_assignment_signature("investigate", investigation) != mission_assignment_signature(
        "escort", investigation
    )


def test_duplicate_offer_hook_accepts_full_offer_call_shape() -> None:
    assert continuity._duplicate_player_offer(
        object(),
        decision=SimpleNamespace(payload={}),
        at=object(),
        command=SimpleNamespace(mode="gameplay"),
        scheduler=object(),
        world_events={},
        record_writes={},
        faction_record={},
    ) is None
