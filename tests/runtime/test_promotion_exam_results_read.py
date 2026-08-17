from __future__ import annotations

import pytest

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api import player_promotion_exam_results_read as results_read


class FakeRepository:
    def __init__(self, pipeline):
        self.pipeline = pipeline

    def read_json(self, path):
        assert path == "state/reg/shinobi-career-pipeline.json"
        return self.pipeline


class FakeOperations:
    def __init__(self, pipeline):
        self.repository = FakeRepository(pipeline)


def _pipeline(cycle_id: str):
    return {
        "history": [
            {
                "kind": "promotion_exam_cycle_phase",
                "cycle_id": cycle_id,
                "profile_ref": "promotion_exam.konoha.chunin",
                "phase": "field_evaluation",
            }
        ]
    }


def test_public_exam_results_are_paged_with_exact_continuation_ref(monkeypatch):
    cycle_id = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
    profile = {
        "id": "promotion_exam.konoha.chunin",
        "result_visibility": {"qualification": "public_after_settlement"},
    }
    rows = [
        {
            "candidate_ref": f"char.candidate_{index:03d}",
            "team_ref": f"team.konoha.{index // 3:03d}",
            "score": 90 + index,
            "threshold": 80,
            "outcome": "pass",
        }
        for index in range(20)
    ]
    monkeypatch.setattr(results_read, "promotion_exam_profiles", lambda repository: [profile])
    monkeypatch.setattr(
        results_read,
        "promotion_exam_evaluation_rows",
        lambda pipeline, requested_cycle_id, phase: rows,
    )
    monkeypatch.setattr(results_read, "_stage_is_settled", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        results_read,
        "_public_identity",
        lambda operations, candidate_ref: (candidate_ref, "Konoha"),
    )

    ref = f"exam-results:{cycle_id}:qualification:0"
    page = results_read._public_results_page(FakeOperations(_pipeline(cycle_id)), object_ref=ref)

    assert page["view"] == "promotion_exam_results_page"
    assert page["object"]["result_count"] == 20
    assert len(page["object"]["rows"]) == 16
    assert page["object"]["next_ref"] == f"exam-results:{cycle_id}:qualification:16"

    second = results_read._public_results_page(
        FakeOperations(_pipeline(cycle_id)),
        object_ref=page["object"]["next_ref"],
    )
    assert len(second["object"]["rows"]) == 4
    assert second["object"]["next_ref"] is None


def test_public_exam_results_ref_does_not_leak_unsettled_stage(monkeypatch):
    cycle_id = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"
    profile = {
        "id": "promotion_exam.konoha.chunin",
        "result_visibility": {"qualification": "public_after_settlement"},
    }
    monkeypatch.setattr(results_read, "promotion_exam_profiles", lambda repository: [profile])
    monkeypatch.setattr(
        results_read,
        "promotion_exam_evaluation_rows",
        lambda pipeline, requested_cycle_id, phase: [],
    )
    monkeypatch.setattr(results_read, "_stage_is_settled", lambda *args, **kwargs: False)

    ref = f"exam-results:{cycle_id}:qualification:0"
    with pytest.raises(OperationError) as exc_info:
        results_read._public_results_page(FakeOperations(_pipeline(cycle_id)), object_ref=ref)
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "object_not_player_visible"
