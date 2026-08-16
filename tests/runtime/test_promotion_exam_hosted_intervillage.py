from __future__ import annotations

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands import promotion_exam_hosted_lifecycle as lifecycle
from shinobi_runtime.commands import promotion_exam_hosted_policy as hosted
from shinobi_runtime.sim.events import CampaignTime


PROFILE = {
    "id": "promotion_exam.konoha.chunin",
    "service_village": "konoha",
    "source_rank": "Genin",
    "phase_offsets_days": {"registration": 0, "qualification": 10},
    "finals_format": {"venue_ref": "place.konoha.academy.assignment.hall"},
    "hosted_exam": {
        "host_village": "konoha",
        "host_arrival_place_ref": "place.konoha",
        "participating_villages": ["konoha", "suna"],
        "foreign_delegations": [
            {
                "delegation_ref": "promotion_exam_delegation.suna.baki",
                "service_village": "suna",
                "selection_authority_ref": "faction_suna",
                "instructor_ref": "canon_baki",
                "candidate_pool_refs": ["canon_gaara", "canon_kankuro", "canon_temari"],
            }
        ],
    },
}


def _person(ref: str, *, village: str = "Suna") -> dict:
    return {
        "schema": "shinobi_character",
        "owner_id": ref,
        "life_status": "active",
        "official_rank_or_status": "genin",
        "village_or_affiliation": village,
        "career_state": {"promotion_eligible": True},
        "condition": {"readiness": "ready"},
        "current_location_id": "place.suna.residential",
        "life_course_state": {
            "deployment": {"home_location_id": "place.suna.residential"},
            "location_history": [],
            "location_changes": 0,
        },
    }


class FakeRepository:
    def __init__(self, first_leg_days: int = 6) -> None:
        self.first_leg_days = first_leg_days

    def read_json(self, _path: str):
        return {
            "payload": {
                "places": [
                    {"id": "place.suna.residential", "route_anchor_ref": "place.suna"},
                    {"id": "place.konoha", "route_anchor_ref": "place.konoha"},
                    {"id": "place.konoha.academy.assignment.hall", "route_anchor_ref": "place.konoha"},
                    {"id": "place.fire.western.border", "route_anchor_ref": "place.fire.western.border"},
                ],
                "routes": [
                    {
                        "from": "place.suna",
                        "to": "place.fire.western.border",
                        "status": "open_controlled",
                        "travel_days_band": [self.first_leg_days, 12],
                    },
                    {
                        "from": "place.fire.western.border",
                        "to": "place.konoha",
                        "status": "guarded",
                        "travel_days_band": [2, 4],
                    },
                ],
            }
        }


class FakePlanner:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.people = {
            "canon_gaara": _person("canon_gaara"),
            "canon_kankuro": _person("canon_kankuro"),
            "canon_temari": _person("canon_temari"),
        }

    def _resolve_covered_owner_view(self, ref, *, cache=None):
        return f"state/char/{ref}.json", "digest", self.people[ref]


def test_hosted_delegation_registers_only_route_feasible_foreign_candidates(monkeypatch):
    monkeypatch.setattr(hosted, "_ORIGINAL_ELIGIBLE", lambda *args, **kwargs: [])
    monkeypatch.setattr(hosted.scheduler, "registered_candidate_refs", lambda *args, **kwargs: ())
    planner = FakePlanner(FakeRepository(first_leg_days=6))

    rows = hosted.eligible_hosted_registrations(
        planner,
        profile=PROFILE,
        pipeline={"history": []},
        cycle_id="promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
        player_id="pc_wei_tang",
    )

    assert rows == [
        {
            "team_ref": "promotion_exam_delegation.suna.baki",
            "instructor_ref": "canon_baki",
            "candidate_refs": ["canon_gaara", "canon_kankuro", "canon_temari"],
        }
    ]
    assert hosted.minimum_route_days(planner.repository, "place.suna.residential", "place.konoha") == 8.0


def test_hosted_delegation_is_not_registered_when_route_cannot_make_qualification(monkeypatch):
    monkeypatch.setattr(hosted, "_ORIGINAL_ELIGIBLE", lambda *args, **kwargs: [])
    monkeypatch.setattr(hosted.scheduler, "registered_candidate_refs", lambda *args, **kwargs: ())
    planner = FakePlanner(FakeRepository(first_leg_days=9))

    rows = hosted.eligible_hosted_registrations(
        planner,
        profile=PROFILE,
        pipeline={"history": []},
        cycle_id="promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
        player_id="pc_wei_tang",
    )

    assert rows == []


def test_hosted_profile_rejects_uninvited_village():
    assert hosted.person_matches_hosted_profile(_person("canon_gaara"), PROFILE)
    assert not hosted.person_matches_hosted_profile(_person("iwa_genin", village="Iwa"), PROFILE)


def test_cross_country_finalist_move_is_repair_only(monkeypatch):
    monkeypatch.setattr(hosted, "_ORIGINAL_STAGE_FINALISTS", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        hosted.integrity,
        "_registration_team_map",
        lambda *args, **kwargs: ({"canon_gaara": "promotion_exam_delegation.suna.baki"}, {"canon_gaara": "canon_baki"}),
    )
    monkeypatch.setattr(hosted.finals, "promotion_exam_finals_candidate_refs", lambda *args, **kwargs: ("canon_gaara",))
    planner = FakePlanner(FakeRepository(first_leg_days=6))
    writes = {}

    with pytest.raises(CommandRejectedError, match="promotion_exam_finalist_not_locally_reachable"):
        hosted.stage_hosted_finalists(
            planner,
            pipeline={"history": []},
            profile=PROFILE,
            cycle_id="promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
            at="SE-0061-07-22T07:29:58",
            player_id="pc_wei_tang",
            record_writes=writes,
        )

    rows = hosted.stage_hosted_finalists(
        planner,
        pipeline={"history": []},
        profile=PROFILE,
        cycle_id="promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
        at="SE-0061-07-22T07:29:58",
        player_id="pc_wei_tang",
        record_writes=writes,
        allow_cross_country_reconciliation=True,
    )
    assert rows[0]["candidate_ref"] == "canon_gaara"
    assert next(iter(writes.values()))["current_location_id"] == "place.konoha.academy.assignment.hall"


def test_repair_travel_preserves_route_duration_for_qualification_elimination():
    arrival, eliminated, returned = lifecycle._repair_travel_times(
        registration_at=CampaignTime.parse("SE-0061-07-01T07:00:00"),
        qualification_at=CampaignTime.parse("SE-0061-07-11T07:00:00"),
        field_at=CampaignTime.parse("SE-0061-07-13T07:00:00"),
        current_time=CampaignTime.parse("SE-0061-07-22T07:29:58"),
        route_days=8.0,
        finalist=False,
        qualification_outcome="fail",
        field_outcome=None,
    )

    assert str(arrival) == "SE-0061-07-09T07:00:00"
    assert str(eliminated) == "SE-0061-07-11T07:00:00"
    assert str(returned) == "SE-0061-07-19T07:00:00"


def test_repair_travel_preserves_route_duration_for_field_elimination_and_finalists():
    registration = CampaignTime.parse("SE-0061-07-01T07:00:00")
    qualification = CampaignTime.parse("SE-0061-07-11T07:00:00")
    field = CampaignTime.parse("SE-0061-07-13T07:00:00")
    current = CampaignTime.parse("SE-0061-07-22T07:29:58")

    arrival, eliminated, returned = lifecycle._repair_travel_times(
        registration_at=registration,
        qualification_at=qualification,
        field_at=field,
        current_time=current,
        route_days=8.0,
        finalist=False,
        qualification_outcome="pass",
        field_outcome="fail",
    )
    assert str(arrival) == "SE-0061-07-09T07:00:00"
    assert str(eliminated) == "SE-0061-07-13T07:00:00"
    assert str(returned) == "SE-0061-07-21T07:00:00"

    finalist_arrival, finalist_elimination, finalist_return = lifecycle._repair_travel_times(
        registration_at=registration,
        qualification_at=qualification,
        field_at=field,
        current_time=current,
        route_days=8.0,
        finalist=True,
        qualification_outcome="pass",
        field_outcome="pass",
    )
    assert str(finalist_arrival) == "SE-0061-07-09T07:00:00"
    assert finalist_elimination is None
    assert finalist_return is None


def test_repair_travel_rejects_route_that_cannot_reach_qualification():
    with pytest.raises(CommandRejectedError, match="promotion_exam_delegation_cannot_arrive_by_stage"):
        lifecycle._repair_travel_times(
            registration_at=CampaignTime.parse("SE-0061-07-01T07:00:00"),
            qualification_at=CampaignTime.parse("SE-0061-07-11T07:00:00"),
            field_at=CampaignTime.parse("SE-0061-07-13T07:00:00"),
            current_time=CampaignTime.parse("SE-0061-07-22T07:29:58"),
            route_days=11.0,
            finalist=False,
            qualification_outcome="fail",
            field_outcome=None,
        )
