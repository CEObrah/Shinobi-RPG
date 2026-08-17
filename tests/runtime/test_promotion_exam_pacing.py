from __future__ import annotations

from shinobi_runtime.commands.promotion_exam_pacing import (
    _academy_cycle_opening_only,
    next_promotion_exam_boundary,
    promotion_exam_schedule_for_cycle,
)
from shinobi_runtime.sim.events import CampaignTime


_CYCLE = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
_PROFILE = "promotion_exam.konoha.chunin"


def _profile():
    return {
        "id": _PROFILE,
        "enabled": True,
        "world_arc_kind": "institutional_promotion_cycle",
        "canon_status": "campaign_institutional_not_future_canon",
        "institution_ref": "institution.konoha.academy",
        "authority_ref": "canon_hiruzen",
        "service_village": "konoha",
        "source_rank": "Genin",
        "target_rank": "Chunin",
        "registration_authority": "active_team_leader",
        "cycle_start_months": [1, 7],
        "phases": [
            "registration",
            "qualification",
            "field_evaluation",
            "finals",
            "promotion_review",
            "closed",
        ],
        "phase_offsets_days": {
            "registration": 0,
            "qualification": 10,
            "field_evaluation": 12,
            "finals": 21,
            "promotion_review": 22,
            "closed": 23,
        },
    }


def _pipeline(*history):
    return {
        "schema": "shinobi-career-pipeline",
        "version": 1,
        "villages": {},
        "history": list(history),
    }


def _registration_row():
    return {
        "kind": "promotion_exam_cycle_phase",
        "at": "SE-0061-07-01T07:00:00",
        "cycle_id": _CYCLE,
        "profile_ref": _PROFILE,
        "phase": "registration",
        "canon_status": "campaign_institutional_not_future_canon",
        "authority_ref": "canon_hiruzen",
    }


class _Repo:
    def read_json(self, path: str):
        if path == "state/reg/shinobi-career-pipeline.json":
            return _pipeline(_registration_row())
        if path == "game/rules/career/promotion-exams.json":
            return {
                "schema": "promotion-exam-rules",
                "version": 2,
                "profiles": {_PROFILE: _profile()},
            }
        raise FileNotFoundError(path)


def test_current_cycle_has_week_scale_phase_dates() -> None:
    schedule = promotion_exam_schedule_for_cycle(_Repo(), cycle_id=_CYCLE)
    assert schedule["qualification"] == "SE-0061-07-11T07:00:00"
    assert schedule["field_evaluation"] == "SE-0061-07-13T07:00:00"
    assert schedule["finals"] == "SE-0061-07-22T07:00:00"
    assert schedule["promotion_review"] == "SE-0061-07-23T07:00:00"
    assert schedule["closed"] == "SE-0061-07-24T07:00:00"


def test_event_seeker_sees_next_exam_phase_before_monthly_academy_review() -> None:
    current = CampaignTime.parse("SE-0061-07-09T07:00:00")
    assert str(next_promotion_exam_boundary(_Repo(), current)) == "SE-0061-07-11T07:00:00"


def test_academy_review_can_open_but_not_advance_active_cycle() -> None:
    opening = _academy_cycle_opening_only(
        _profile(),
        _pipeline(),
        CampaignTime.parse("SE-0061-07-01T07:00:00"),
    )
    assert opening == (_CYCLE, "registration")
    assert _academy_cycle_opening_only(
        _profile(),
        _pipeline(_registration_row()),
        CampaignTime.parse("SE-0061-08-01T07:00:00"),
    ) is None
