from __future__ import annotations

import copy

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_named_training_exam_repair import _REPAIR_ID, _qualification_cycle
from shinobi_runtime.commands.named_service_development import (
    _qualifying_status,
    service_start,
    settle_service_development,
)
from shinobi_runtime.commands.team_training_cursor_reconciliation import _team_review_cursor_skip
from shinobi_runtime.sim.events import CampaignTime


POLICY = {
    "active_hours_per_month": 24,
    "historical_hours_per_full_week": 6,
    "target_cycle": [
        "operational_skills.team_coordination",
        "martial_skills.movement",
        "operational_skills.tactics",
        "attributes.awareness",
    ],
    "eligible_status_tokens": ["genin", "chunin", "jonin", "shinobi", "ninja", "anbu", "kage", "sannin", "missing-nin"],
}


def person() -> dict:
    return {
        "schema": "shinobi_character",
        "life_status": "active",
        "official_rank_or_status": "Genin",
        "condition": {"readiness": "ready"},
        "attributes": {"awareness": 40},
        "martial_skills": {"movement": 40},
        "operational_skills": {"team_coordination": 40, "tactics": 40},
        "aptitude": {"physical_learning": 100, "tactical_learning": 100},
        "life_course_state": {
            "location_history": [{"at": "SE-0061-02-05T07:00:00", "location_id": "place.test"}],
            "rank_history": [
                {"at": "SE-0061-02-05T07:00:00", "rank": "academy"},
                {"at": "SE-0061-03-01T07:00:00", "rank": "Genin"},
            ],
        },
    }


def test_team_review_cursor_no_longer_vetoes_stale_or_partial_windows() -> None:
    interval = CampaignTime.parse("SE-0061-08-01T07:00:00")
    assert _team_review_cursor_skip(CampaignTime.parse("SE-0061-02-05T07:00:00"), interval) is None
    assert _team_review_cursor_skip(interval, interval) is None
    assert _team_review_cursor_skip(CampaignTime.parse("SE-0061-08-03T07:00:00"), interval) is None


def test_service_start_waits_for_exact_shinobi_service() -> None:
    row = person()
    start = service_start(row, CampaignTime.parse("SE-0061-02-05T07:00:00"), POLICY)
    assert str(start) == "SE-0061-03-01T07:00:00"


def test_historical_service_catchup_uses_full_weeks_and_existing_bank() -> None:
    row = person()
    entry = {"owner_type": "character", "resolved_through": "SE-0061-03-01T07:00:00", "credits": {}}
    outcome = settle_service_development(
        row,
        entry,
        owner_ref="canon_test",
        start=CampaignTime.parse("SE-0061-03-01T07:00:00"),
        through=CampaignTime.parse("SE-0061-03-29T07:00:00"),
        policy=POLICY,
        historical=True,
    )
    assert outcome["hours"] == "24"
    assert str(entry["resolved_through"]) == "SE-0061-03-29T07:00:00"
    assert sum(item["points_gained"] for item in outcome["outcomes"]) > 0
    assert entry["credits"]
    assert all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in entry["credits"].values())


def test_ordinary_service_review_is_bounded_and_proportional() -> None:
    row = person()
    entry = {"owner_type": "character", "resolved_through": "SE-0061-04-01T07:00:00", "credits": {}}
    outcome = settle_service_development(
        row,
        entry,
        owner_ref="canon_test",
        start=CampaignTime.parse("SE-0061-04-01T07:00:00"),
        through=CampaignTime.parse("SE-0061-04-16T07:00:00"),
        policy=POLICY,
        historical=False,
    )
    assert outcome["hours"] == "12.0"


def test_named_service_eligibility_excludes_nonservice_or_unready_people() -> None:
    row = person()
    assert _qualifying_status(row, POLICY)
    academy = copy.deepcopy(row)
    academy["official_rank_or_status"] = "Academy Student"
    assert not _qualifying_status(academy, POLICY)
    injured = copy.deepcopy(row)
    injured["condition"]["readiness"] = "medical_hold"
    assert not _qualifying_status(injured, POLICY)
    dead = copy.deepcopy(row)
    dead["life_status"] = "dead"
    assert not _qualifying_status(dead, POLICY)


def _old_pipeline() -> dict:
    rows = []
    for index in range(42):
        passed = index < 26
        rows.append({
            "kind": "promotion_exam_evaluation",
            "at": "SE-0061-08-05T07:29:58",
            "cycle_id": "promotion_exam_cycle.test",
            "profile_ref": "promotion_exam.konoha.chunin",
            "phase": "qualification",
            "candidate_ref": f"candidate.{index:02d}",
            "score": 60 if passed else 59,
            "threshold": 60,
            "outcome": "pass" if passed else "fail",
        })
    return {"schema": "shinobi-career-pipeline", "version": 1, "history": rows}


def test_guarded_repair_accepts_only_exact_old_qualification_shape() -> None:
    cycle, rows = _qualification_cycle(_old_pipeline())
    assert cycle == "promotion_exam_cycle.test"
    assert len(rows) == 42

    changed = _old_pipeline()
    changed["history"][0]["outcome"] = "fail"
    with pytest.raises(CommandRejectedError, match="named_training_exam_repair_source_not_exact"):
        _qualification_cycle(changed)


def test_guarded_repair_rejects_registered_recalibration_receipt() -> None:
    repaired = _old_pipeline()
    repaired["history"].append({
        "kind": "promotion_exam_recalibration",
        "recalibration_ref": _REPAIR_ID,
    })
    with pytest.raises(CommandRejectedError, match="named_training_exam_repair_source_not_exact"):
        _qualification_cycle(repaired)
