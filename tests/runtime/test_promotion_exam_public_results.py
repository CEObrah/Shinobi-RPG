from __future__ import annotations

from shinobi_runtime.api import player_promotion_exam_participation_projection as projection


class FakeOperations:
    pass


def test_public_stage_results_return_summaries_and_paged_read_refs_not_bulk_rows():
    cycle_id = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
    pipeline = {
        "history": [
            {
                "kind": "promotion_exam_evaluation",
                "at": "SE-0061-07-11T07:00:00",
                "cycle_id": cycle_id,
                "profile_ref": "promotion_exam.konoha.chunin",
                "phase": "qualification",
                "team_ref": "promotion_exam_delegation.suna.baki",
                "evaluator_ref": "canon_hiruzen",
                "candidate_ref": "canon_gaara",
                "score": 81,
                "threshold": 78,
                "outcome": "pass",
                "canon_status": "campaign_institutional_not_future_canon",
            },
            {
                "kind": "promotion_exam_evaluation",
                "at": "SE-0061-07-13T07:00:00",
                "cycle_id": cycle_id,
                "profile_ref": "promotion_exam.konoha.chunin",
                "phase": "field_evaluation",
                "team_ref": "team.konoha.fujin",
                "evaluator_ref": "canon_hiruzen",
                "candidate_ref": "char.riku_hyuga",
                "score": 96,
                "threshold": 82,
                "outcome": "pass",
                "canon_status": "campaign_institutional_not_future_canon",
            },
            {
                "kind": "promotion_exam_cycle_phase",
                "at": "SE-0061-07-22T07:00:00",
                "cycle_id": cycle_id,
                "profile_ref": "promotion_exam.konoha.chunin",
                "phase": "finals",
            },
        ]
    }
    profile = {
        "phases": [
            "registration",
            "qualification",
            "field_evaluation",
            "finals",
            "promotion_review",
            "closed",
        ],
        "result_visibility": {
            "qualification": "public_after_settlement",
            "field_evaluation": "public_after_settlement",
        },
    }

    result = projection._public_stage_results(FakeOperations(), pipeline, profile, cycle_id)

    assert result["result_count"] == 2
    assert result["stage_summaries"]["qualification"] == {
        "candidate_count": 1,
        "pass_count": 1,
        "fail_count": 0,
    }
    assert result["stage_summaries"]["field_evaluation"] == {
        "candidate_count": 1,
        "pass_count": 1,
        "fail_count": 0,
    }
    assert result["read_refs"] == {
        "qualification": f"exam-results:{cycle_id}:qualification:0",
        "field_evaluation": f"exam-results:{cycle_id}:field_evaluation:0",
    }
    assert "stages" not in result


def test_public_after_settlement_does_not_leak_partial_stage_scores(monkeypatch):
    cycle_id = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
    pipeline = {
        "history": [
            {
                "kind": "promotion_exam_cycle_phase",
                "at": "SE-0061-07-11T07:00:00",
                "cycle_id": cycle_id,
                "profile_ref": "promotion_exam.konoha.chunin",
                "phase": "qualification",
            },
            {
                "kind": "promotion_exam_evaluation",
                "at": "SE-0061-07-11T07:05:00",
                "cycle_id": cycle_id,
                "profile_ref": "promotion_exam.konoha.chunin",
                "phase": "qualification",
                "team_ref": "promotion_exam_delegation.suna.baki",
                "candidate_ref": "canon_gaara",
                "score": 81,
                "threshold": 78,
                "outcome": "pass",
            },
        ]
    }
    profile = {
        "phases": ["registration", "qualification", "field_evaluation", "finals"],
        "result_visibility": {"qualification": "public_after_settlement"},
    }
    monkeypatch.setattr(
        projection,
        "promotion_exam_stage_candidate_refs",
        lambda *args, **kwargs: ("canon_gaara", "char.riku_hyuga"),
    )

    result = projection._public_stage_results(FakeOperations(), pipeline, profile, cycle_id)

    assert result["stage_summaries"] == {}
    assert result["read_refs"] == {}
    assert result["result_count"] == 0
