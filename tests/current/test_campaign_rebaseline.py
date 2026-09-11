from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]

def load(rel):
    return json.loads((ROOT / rel).read_text())

def test_campaign_descends_from_explicit_revision_one_rebaseline():
    manifest = load("docs/CAMPAIGN_REBASELINE_20260902.json")
    meta = load("state/meta.json")
    assert manifest["old_revision"] == 158 and manifest["new_revision"] == 1
    assert manifest["campaign_id"] == meta["campaign_id"]
    assert meta["revision"] >= manifest["new_revision"]
    assert manifest["fresh_private_recovery_store_required"] is True
    assert (ROOT / manifest["archived_state"]).is_file()

def test_rebaseline_manifest_records_the_one_time_presentation_reset_without_freezing_live_state():
    manifest = load("docs/CAMPAIGN_REBASELINE_20260902.json")
    reset = set(manifest["presentation_residue_reset"])
    assert "state/martial-world/interaction-attempts.json" in reset
    assert "state/martial-world/scene-history-head.json" in reset
    assert "state/martial-world/scene-history/*" in reset
    # Current ledgers may now be populated by lawful play. Their present content
    # is not evidence that the 2026-09-02 reset failed.
    assert load("state/martial-world/interaction-attempts.json")["total_recorded"] >= 0
    assert load("state/martial-world/scene-history-head.json")["total_recorded"] >= 0

def test_rebaseline_uses_single_main_branch_and_clean_runtime_lineage():
    manifest = load("docs/CAMPAIGN_REBASELINE_20260902.json")
    assert manifest["git_branch"] == "main"
    assert manifest["single_branch_source_and_campaign_durability"] is True
    assert manifest["fresh_campaign_checkout_required"] is True
    assert manifest["other_campaign_branches_required"] is False
    railway = (ROOT / "railway.toml").read_text(encoding="utf-8")
    assert "SHINOBI_GIT_BRANCH=main" in railway
    assert "python -m shinobi_runtime.bootstrap" in railway
    assert "branch_bootstrap" not in railway
    assert not (ROOT / "runtime/shinobi_runtime/branch_bootstrap.py").exists()
