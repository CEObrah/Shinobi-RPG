from __future__ import annotations

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.promotion_exam_cycle import (
    _exam_boundaries,
    _shift_pipeline_rank,
    _validate_exact_promotion_transition,
)
from shinobi_runtime.sim.events import CampaignTime


def _candidate(*, eligible: bool = True) -> dict:
    return {
        "official_rank_or_status": "Genin",
        "career_state": {"promotion_eligible": eligible},
    }


def test_exact_promotion_requires_evidence_and_one_rank_step() -> None:
    assert _validate_exact_promotion_transition(
        _candidate(), target_rank_or_status="Chunin"
    ) == ("genin", "chunin")
    with pytest.raises(CommandRejectedError, match="career_promotion_evidence_required"):
        _validate_exact_promotion_transition(
            _candidate(eligible=False), target_rank_or_status="Chunin"
        )
    with pytest.raises(CommandRejectedError, match="career_rank_skip_forbidden"):
        _validate_exact_promotion_transition(
            _candidate(), target_rank_or_status="Jonin"
        )


def test_exam_cycle_opens_on_registered_calendar_boundary() -> None:
    profile = {"cycle_months": [1, 7], "cycle_day": 1, "cycle_hour": 9}
    assert _exam_boundaries(
        profile,
        after=CampaignTime.parse("SE-0061-06-11T01:14:37"),
        through=CampaignTime.parse("SE-0061-07-02T00:00:00"),
    ) == (CampaignTime.parse("SE-0061-07-01T09:00:00"),)


def test_exact_rank_accounting_conserves_headcount() -> None:
    pipeline = {
        "schema": "shinobi-career-pipeline",
        "version": 1,
        "villages": {
            "konoha": {"rank_counts": {"genin": 10, "chunin": 4, "jonin": 2}}
        },
        "history": [],
    }
    _shift_pipeline_rank(
        pipeline, village="konoha", source="genin", target="chunin"
    )
    assert pipeline["villages"]["konoha"]["rank_counts"] == {
        "genin": 9,
        "chunin": 5,
        "jonin": 2,
    }
