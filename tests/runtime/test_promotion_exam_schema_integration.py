from __future__ import annotations

import json
from pathlib import Path

import pytest

from shinobi_runtime.store.template_validation import RegisteredTemplateValidator

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


def test_template_closes_persisted_combat_effect_plan_shape():
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    objects = template["object_contracts"]
    arrays = template["array_contracts"]
    combat = "/history/*/combat_record"

    assert objects[combat]["mode"] == "closed"
    assert set(objects[combat]["allowed_keys"]) == {
        "combat_ref",
        "transaction_ref",
        "scale",
        "resolution_mode",
        "wake_triggers",
        "exchange_effects",
        "participant_effects",
        "objective_effects",
        "victorious_side_refs",
        "status",
        "successor_boundaries",
        "rng_receipts",
    }

    nested_objects = {
        combat + "/wake_triggers/*",
        combat + "/exchange_effects/*",
        combat + "/participant_effects/*",
        combat + "/participant_effects/*/before_personnel",
        combat + "/participant_effects/*/after_personnel",
        combat + "/participant_effects/*/before_resources/*",
        combat + "/participant_effects/*/after_resources/*",
        combat + "/participant_effects/*/before_position",
        combat + "/participant_effects/*/after_position",
        combat + "/objective_effects/*",
        combat + "/successor_boundaries/*",
        combat + "/rng_receipts/*",
    }
    assert nested_objects <= set(objects)
    assert all(objects[path]["mode"] == "closed" for path in nested_objects)

    nested_arrays = {
        combat + "/wake_triggers",
        combat + "/exchange_effects",
        combat + "/participant_effects",
        combat + "/objective_effects",
        combat + "/victorious_side_refs",
        combat + "/successor_boundaries",
        combat + "/rng_receipts",
        combat + "/participant_effects/*/before_resources",
        combat + "/participant_effects/*/after_resources",
        combat + "/successor_boundaries/*/participant_refs",
        combat + "/successor_boundaries/*/authoritative_owner_refs",
    }
    assert nested_arrays <= set(arrays)
    assert all(
        contract["mode"] in {"closed", "open_map"}
        for path, contract in objects.items()
        if path.startswith(combat)
    )


def _finals_bout() -> dict:
    return {
        "kind": "promotion_exam_bout",
        "at": "SE-0061-07-22T07:29:58",
        "cycle_id": "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07",
        "profile_ref": "promotion_exam.konoha.chunin",
        "phase": "finals",
        "bout_ref": "promotion_exam_bout.fixture.r1.m0",
        "round_index": 1,
        "match_index": 0,
        "candidate_refs": ["canon_sasuke", "canon_gaara"],
        "winner_ref": "canon_sasuke",
        "loser_ref": "canon_gaara",
        "resolution_method": "combat_stoppage",
        "resolution_mode": "kernel",
        "victorious_side_refs": ["side:promotion_exam:0"],
        "judge_scores": {"canon_sasuke": 91, "canon_gaara": 88},
        "styles": {
            "canon_sasuke": {
                "martial_focus": "taijutsu",
                "domain_focus": "ninjutsu",
                "featured_method_ref": "chidori",
            },
            "canon_gaara": {
                "martial_focus": None,
                "domain_focus": "ninjutsu",
                "featured_method_ref": None,
            },
        },
        "combat_record": {
            "combat_ref": "combat.exam.fixture",
            "transaction_ref": "tx.gameplay.fixture",
            "scale": "duel",
            "resolution_mode": "kernel",
            "wake_triggers": [],
            "exchange_effects": [],
            "participant_effects": [],
            "objective_effects": [],
            "victorious_side_refs": ["side:promotion_exam:0"],
            "status": "completed",
            "successor_boundaries": [],
            "rng_receipts": [],
        },
        "duration_seconds": 30,
        "examiner_ref": "canon_hiruzen",
        "canon_status": "campaign_institutional_not_future_canon",
    }


def test_persisted_finals_row_passes_schema_and_registered_template_validation():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    owner = _owner([_finals_bout()])

    jsonschema.Draft202012Validator(schema).validate(owner)
    RegisteredTemplateValidator._validate_document(
        owner, template, label="finals fixture"
    )

    style_contract = template["object_contracts"]["/history/*/styles/*"]
    assert style_contract["mode"] == "closed"
    assert set(style_contract["allowed_keys"]) == {
        "martial_focus",
        "domain_focus",
        "featured_method_ref",
    }
