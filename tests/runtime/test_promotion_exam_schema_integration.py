from __future__ import annotations

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "game/schemas/shinobi-career-pipeline.schema.json"
TEMPLATE_PATH = ROOT / "runtime/contracts/templates/shinobi-career-pipeline.template.json"


def _owner(history: list[dict]) -> dict:
    return {
        "schema": "shinobi-career-pipeline",
        "version": 1,
        "last_review_at": "SE-0061-07-22T07:29:58",
        "villages": {
            "konoha": {
                "service_pool_ref": "pool.konoha.shinobi_service",
                "force_ref": "force.konoha.shinobi",
                "rank_counts": {"genin": 100, "chunin": 50, "jonin": 25},
                "promotion_credit_ppm": {
                    "genin_to_chunin": 0,
                    "chunin_to_jonin": 0,
                },
            }
        },
        "history": history,
        "provenance": "test fixture",
    }


def _registration(mode: str) -> dict:
    return {
        "kind": "promotion_exam_registration",
        "at": "SE-0061-07-01T07:00:00",
        "cycle_id": "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
        "profile_ref": "promotion_exam.konoha.chunin",
        "team_ref": "team.konoha.generated.7509160457",
        "instructor_ref": "canon_kakashi",
        "candidate_refs": ["canon_naruto", "canon_sakura", "canon_sasuke"],
        "canon_status": "campaign_institutional_not_future_canon",
        "registration_mode": mode,
    }


def _evaluation(mode: str) -> dict:
    return {
        "kind": "promotion_exam_evaluation",
        "at": "SE-0061-07-11T07:00:00",
        "cycle_id": "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
        "profile_ref": "promotion_exam.konoha.chunin",
        "phase": "qualification",
        "team_ref": "team.konoha.generated.7509160457",
        "evaluator_ref": "canon_hiruzen",
        "candidate_ref": "canon_sasuke",
        "score": 92,
        "threshold": 78,
        "outcome": "pass",
        "canon_status": "campaign_institutional_not_future_canon",
        "evaluation_mode": mode,
    }


def _aggregate_row(index: int) -> dict:
    return {
        "kind": "aggregate_rank_progression",
        "at": f"SE-0060-01-{(index % 28) + 1:02d}T07:00:00",
        "promotions": {
            "konoha": {
                "genin_to_chunin": 0,
                "chunin_to_jonin": 0,
            }
        },
        "headcount_before": 175,
        "headcount_after": 175,
    }


def test_registration_and_evaluation_provenance_modes_are_schema_valid():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    validator.validate(_owner([_registration("institution_autonomous_team_submission")]))
    validator.validate(_owner([_registration("ooc_dev_current_cycle_reconciliation")]))
    validator.validate(_owner([_evaluation("institution_autonomous_exact_candidate")]))
    validator.validate(_owner([_evaluation("ooc_dev_current_cycle_reconciliation")]))


def test_active_career_owner_schema_has_no_512_row_validity_ceiling():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    history = [_aggregate_row(index) for index in range(700)]

    validator.validate(_owner(history))
    assert "maxItems" not in schema["properties"]["history"]


def test_template_registers_provenance_modes_without_hard_history_limit():
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    row_contract = template["object_contracts"]["/history/*"]
    allowed = set(row_contract["allowed_keys"])
    ordered = set(row_contract["canonical_order"])

    assert {"registration_mode", "evaluation_mode"} <= allowed
    assert {"registration_mode", "evaluation_mode"} <= ordered
    assert template["type_contracts"]["/history/*/registration_mode"] == ["string"]
    assert template["type_contracts"]["/history/*/evaluation_mode"] == ["string"]
    assert "max_items" not in template["array_contracts"]["/history"]
