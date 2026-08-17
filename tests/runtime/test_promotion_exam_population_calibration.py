from __future__ import annotations

from pathlib import Path

from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.commands import promotion_exam_evaluation as evaluation
from shinobi_runtime.commands.promotion_exam_scheduler import promotion_exam_profiles
from shinobi_runtime.store import RepositoryStore


ROOT = Path(__file__).resolve().parents[2]
CYCLE = "promotion_exam_cycle.promotion_exam.konoha.chunin.0061-07"


def _registered_refs(pipeline):
    refs = []
    for row in pipeline["history"]:
        if (
            isinstance(row, dict)
            and row.get("kind") == "promotion_exam_registration"
            and row.get("cycle_id") == CYCLE
        ):
            refs.extend(row["candidate_refs"])
    return tuple(dict.fromkeys(refs))


def _persons(repo, refs):
    planner = CampaignCommandPlanner(repo)
    result = {}
    cache = _OwnerResolutionCache()
    for ref in refs:
        _path, _digest, person = planner._resolve_covered_owner_view(ref, cache=cache)
        result[ref] = person
    return result


def test_current_exam_population_has_non_degenerate_stage_calibration():
    repo = RepositoryStore(ROOT)
    pipeline = repo.read_json("state/reg/shinobi-career-pipeline.json")
    profile = promotion_exam_profiles(repo)[0]
    refs = _registered_refs(pipeline)
    assert len(refs) >= 30
    persons = _persons(repo, refs)

    qualification = profile["evaluation_stages"]["qualification"]
    qualified = [
        ref for ref in refs
        if evaluation._score_candidate_details(persons[ref], qualification)["outcome"] == "pass"
    ]
    qualification_rate = len(qualified) / len(refs)
    assert 0.35 <= qualification_rate <= 0.75

    generated = [ref for ref in refs if ref.startswith("char.exam_")]
    generated_passes = [ref for ref in generated if ref in qualified]
    assert generated
    assert 0 < len(generated_passes) < len(generated)

    field = profile["evaluation_stages"]["field_evaluation"]
    field_passes = [
        ref for ref in qualified
        if evaluation._score_candidate_details(persons[ref], field)["outcome"] == "pass"
    ]
    field_rate = len(field_passes) / len(qualified)
    assert 0.30 <= field_rate <= 0.90


def test_generated_candidate_scale_is_not_ceiling_locked_to_stage_thresholds():
    repo = RepositoryStore(ROOT)
    profile = promotion_exam_profiles(repo)[0]
    calibration = profile["hosted_exam"]["generated_candidate_calibration"]
    maximum = calibration["maximum"]
    assert maximum > profile["evaluation_stages"]["qualification"]["threshold"]
    assert maximum > profile["evaluation_stages"]["field_evaluation"]["threshold"]
    assert maximum > 82
