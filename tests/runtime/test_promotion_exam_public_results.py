from __future__ import annotations

from shinobi_runtime.api import player_promotion_exam_participation_projection as projection


class FakeOperations:
    def __init__(self):
        self.people = {
            "canon_gaara": {"name": "Gaara", "village_or_affiliation": "Suna"},
            "char.riku_hyuga": {"name": "Riku Hyuga", "village_or_affiliation": "Konoha"},
        }

    def _owner_record(self, ref):
        return f"state/char/{ref}.json", self.people[ref]


def test_public_stage_results_include_identity_village_score_threshold_and_outcome():
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
        ]
    }
    profile = {
        "result_visibility": {
            "qualification": "public_after_settlement",
            "field_evaluation": "public_after_settlement",
        }
    }

    result = projection._public_stage_results(FakeOperations(), pipeline, profile, cycle_id)

    assert result["result_count"] == 2
    assert result["results_truncated"] is False
    gaara = result["stages"]["qualification"][0]
    assert gaara == {
        "candidate_ref": "canon_gaara",
        "candidate_name": "Gaara",
        "village": "Suna",
        "team_ref": "promotion_exam_delegation.suna.baki",
        "score": 81,
        "threshold": 78,
        "outcome": "pass",
    }
    riku = result["stages"]["field_evaluation"][0]
    assert riku["candidate_name"] == "Riku Hyuga"
    assert riku["score"] == 96
    assert riku["outcome"] == "pass"
