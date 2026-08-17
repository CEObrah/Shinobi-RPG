#!/usr/bin/env python3
"""Deterministically repair promotion-exam evaluation rows after a scoring-model defect.

This is a campaign migration, not gameplay. It never advances campaign time,
regenerates candidates, changes capability, or alters registration. Each repaired
row preserves its prior effective values in ``legacy_evaluation`` and records a
stable recalibration reference before the current source score is applied.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from shinobi_runtime.commands.core import _OwnerResolutionCache  # noqa: E402
from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner  # noqa: E402
from shinobi_runtime.commands import promotion_exam_evaluation as evaluation  # noqa: E402
from shinobi_runtime.commands.promotion_exam_scheduler import promotion_exam_profiles  # noqa: E402
from shinobi_runtime.store import RegisteredSchemaValidator, RepositoryStore  # noqa: E402
from shinobi_runtime.store.template_validation import RegisteredTemplateValidator  # noqa: E402

CAREER_PATH = "state/reg/shinobi-career-pipeline.json"
META_PATH = "state/meta.json"
RECALIBRATION_REF = "migration.promotion_exam_scoring.competency_lanes_v2.0061-08-05"


def _profile_map(repo: RepositoryStore):
    return {str(row["id"]): row for row in promotion_exam_profiles(repo)}


def recalibrate(root: Path, *, write: bool) -> dict[str, object]:
    repo = RepositoryStore(root)
    meta = copy.deepcopy(repo.read_json(META_PATH))
    pipeline = copy.deepcopy(repo.read_json(CAREER_PATH))
    profiles = _profile_map(repo)
    history = pipeline.get("history")
    if not isinstance(history, list):
        raise RuntimeError("shinobi_career_pipeline_invalid")

    planner = CampaignCommandPlanner(repo)
    cache = _OwnerResolutionCache()
    repaired: list[str] = []
    outcomes_before = {"pass": 0, "fail": 0}
    outcomes_after = {"pass": 0, "fail": 0}
    cycle_ids: set[str] = set()
    phases: set[str] = set()

    for row in history:
        if not isinstance(row, dict) or row.get("kind") != "promotion_exam_evaluation":
            continue
        profile = profiles.get(row.get("profile_ref"))
        phase = row.get("phase")
        if not isinstance(profile, dict) or not isinstance(phase, str):
            continue
        config = profile.get("evaluation_stages", {}).get(phase)
        if not isinstance(config, dict) or config.get("scoring_model") != "competency_lanes_v2":
            continue
        if row.get("recalibration_ref") == RECALIBRATION_REF:
            continue
        candidate_ref = row.get("candidate_ref")
        if not isinstance(candidate_ref, str) or not candidate_ref:
            raise RuntimeError("promotion_exam_candidate_ref_invalid")
        _path, _digest, person = planner._resolve_covered_owner_view(candidate_ref, cache=cache)
        details = evaluation._score_candidate_details(person, evaluation._evaluation_config(profile, phase))
        old_score = row.get("score")
        old_threshold = row.get("threshold")
        old_outcome = row.get("outcome")
        if (
            isinstance(old_score, bool) or not isinstance(old_score, int)
            or isinstance(old_threshold, bool) or not isinstance(old_threshold, int)
            or old_outcome not in ("pass", "fail")
        ):
            raise RuntimeError("promotion_exam_legacy_evaluation_invalid")
        outcomes_before[str(old_outcome)] += 1
        outcomes_after[str(details["outcome"])] += 1
        row["legacy_evaluation"] = {
            "score": old_score,
            "threshold": old_threshold,
            "outcome": old_outcome,
            "scoring_model": str(row.get("scoring_model") or "weighted_components_v1"),
            "scoring_version": int(row.get("scoring_version") or 1),
        }
        row["score"] = int(details["score"])
        row["threshold"] = int(details["threshold"])
        row["outcome"] = str(details["outcome"])
        row["scoring_model"] = str(details["scoring_model"])
        row["scoring_version"] = int(details["scoring_version"])
        row["lane_scores"] = dict(details["lane_scores"])
        row["recalibration_ref"] = RECALIBRATION_REF
        repaired.append(candidate_ref)
        if isinstance(row.get("cycle_id"), str):
            cycle_ids.add(row["cycle_id"])
        phases.add(phase)

    if not repaired:
        return {
            "changed": False,
            "revision_before": meta.get("revision"),
            "revision_after": meta.get("revision"),
            "world_time": meta.get("time"),
            "repaired_candidates": 0,
        }
    if len(cycle_ids) != 1 or phases != {"qualification"}:
        raise RuntimeError("promotion_exam_recalibration_scope_unexpected")
    cycle_id = next(iter(cycle_ids))
    profile_ref = next(
        str(row["profile_ref"])
        for row in history
        if isinstance(row, dict)
        and row.get("kind") == "promotion_exam_evaluation"
        and row.get("cycle_id") == cycle_id
    )
    history.append({
        "kind": "promotion_exam_recalibration",
        "at": str(meta["time"]),
        "cycle_id": cycle_id,
        "profile_ref": profile_ref,
        "phase": "qualification",
        "authority_ref": "source_repair",
        "candidate_refs": sorted(set(repaired)),
        "action": "Recalibrated already-settled qualification evidence from the defective raw weighted model to competency_lanes_v2 while preserving every legacy score and outcome in-row.",
        "evaluation_mode": "deterministic_campaign_migration",
        "recalibration_ref": RECALIBRATION_REF,
        "canon_status": "campaign_institutional_not_future_canon"
    })

    revision_before = meta.get("revision")
    if isinstance(revision_before, bool) or not isinstance(revision_before, int) or revision_before < 0:
        raise RuntimeError("campaign_meta_invalid")
    meta["revision"] = revision_before + 1

    template = repo.read_json("runtime/contracts/templates/shinobi-career-pipeline.template.json")
    RegisteredTemplateValidator._validate_document(pipeline, template, label=CAREER_PATH)
    schema_validator = RegisteredSchemaValidator(repo)
    validator = schema_validator.validators.get("shinobi-career-pipeline")
    if validator is None:
        raise RuntimeError("shinobi_career_pipeline_schema_missing")
    validator.validate(pipeline)

    if write:
        (root / CAREER_PATH).write_text(json.dumps(pipeline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (root / META_PATH).write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "changed": True,
        "revision_before": revision_before,
        "revision_after": meta["revision"],
        "world_time": meta["time"],
        "cycle_id": cycle_id,
        "phase": "qualification",
        "repaired_candidates": len(repaired),
        "outcomes_before": outcomes_before,
        "outcomes_after": outcomes_after,
        "recalibration_ref": RECALIBRATION_REF,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=str(ROOT))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = recalibrate(Path(args.root).resolve(), write=args.write)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
