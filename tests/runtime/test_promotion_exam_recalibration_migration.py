from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/recalibrate_promotion_exam_state.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("promotion_exam_recalibration_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module




def _restore_pre_recalibration_fixture(root: Path, tool) -> None:
    """Reconstruct the pre-fix committed evidence from preserved legacy rows."""
    meta_path = root / "state/meta.json"
    pipeline_path = root / "state/reg/shinobi-career-pipeline.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    history = pipeline["history"]
    restored = 0
    kept = []
    for row in history:
        if isinstance(row, dict) and row.get("kind") == "promotion_exam_recalibration" and row.get("recalibration_ref") == tool.RECALIBRATION_REF:
            continue
        if (
            isinstance(row, dict)
            and row.get("kind") == "promotion_exam_evaluation"
            and row.get("phase") == "qualification"
            and row.get("recalibration_ref") == tool.RECALIBRATION_REF
        ):
            legacy = row.get("legacy_evaluation")
            assert isinstance(legacy, dict)
            row["score"] = legacy["score"]
            row["threshold"] = legacy["threshold"]
            row["outcome"] = legacy["outcome"]
            row["scoring_model"] = legacy["scoring_model"]
            row["scoring_version"] = legacy["scoring_version"]
            row.pop("lane_scores", None)
            row.pop("recalibration_ref", None)
            row.pop("legacy_evaluation", None)
            restored += 1
        kept.append(row)
    assert restored == 42
    pipeline["history"] = kept
    assert isinstance(meta.get("revision"), int) and meta["revision"] > 0
    meta["revision"] -= 1
    pipeline_path.write_text(json.dumps(pipeline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def test_current_qualification_recalibration_is_narrow_idempotent_and_time_preserving(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"))
    tool = _load_tool()
    _restore_pre_recalibration_fixture(root, tool)

    before_meta = json.loads((root / "state/meta.json").read_text(encoding="utf-8"))
    before_population = (root / "state/population/registry.json").read_bytes()
    before_player = (root / "state/player.json").read_bytes()
    before_pipeline = json.loads((root / "state/reg/shinobi-career-pipeline.json").read_text(encoding="utf-8"))
    before_rows = [
        copy.deepcopy(row)
        for row in before_pipeline["history"]
        if isinstance(row, dict)
        and row.get("kind") == "promotion_exam_evaluation"
        and row.get("phase") == "qualification"
    ]
    assert len(before_rows) == 42

    preview = tool.recalibrate(root, write=False)
    assert preview["changed"] is True
    assert preview["repaired_candidates"] == 42
    assert preview["outcomes_before"] == {"pass": 5, "fail": 37}
    assert preview["outcomes_after"] == {"pass": 26, "fail": 16}
    assert json.loads((root / "state/meta.json").read_text(encoding="utf-8")) == before_meta

    applied = tool.recalibrate(root, write=True)
    assert applied == preview
    after_meta = json.loads((root / "state/meta.json").read_text(encoding="utf-8"))
    assert after_meta["time"] == before_meta["time"]
    assert after_meta["revision"] == before_meta["revision"] + 1
    assert (root / "state/population/registry.json").read_bytes() == before_population
    assert (root / "state/player.json").read_bytes() == before_player

    after_pipeline = json.loads((root / "state/reg/shinobi-career-pipeline.json").read_text(encoding="utf-8"))
    after_rows = [
        row for row in after_pipeline["history"]
        if isinstance(row, dict)
        and row.get("kind") == "promotion_exam_evaluation"
        and row.get("phase") == "qualification"
    ]
    before_by_candidate = {row["candidate_ref"]: row for row in before_rows}
    assert len(after_rows) == len(before_rows)
    assert sum(row["outcome"] == "pass" for row in after_rows) == 26
    for row in after_rows:
        legacy = row["legacy_evaluation"]
        original = before_by_candidate[row["candidate_ref"]]
        assert legacy["score"] == original["score"]
        assert legacy["threshold"] == original["threshold"]
        assert legacy["outcome"] == original["outcome"]
        assert row["scoring_model"] == "competency_lanes_v2"
        assert row["scoring_version"] == 2
        assert row["recalibration_ref"] == tool.RECALIBRATION_REF
        assert row["lane_scores"]

    second = tool.recalibrate(root, write=True)
    assert second["changed"] is False
    assert second["revision_before"] == second["revision_after"] == after_meta["revision"]
    assert json.loads((root / "state/meta.json").read_text(encoding="utf-8"))["time"] == before_meta["time"]
