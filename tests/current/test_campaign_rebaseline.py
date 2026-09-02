from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]

def load(rel):
    return json.loads((ROOT / rel).read_text())

def test_canonical_campaign_is_explicit_revision_one_rebaseline():
    assert load("state/meta.json")["revision"] == 1
    ledger = load("state/martial-world/interaction-attempts.json")
    assert ledger["total_recorded"] == 0 and ledger["attempts"] == []
    head = load("state/martial-world/scene-history-head.json")
    assert head["total_recorded"] == 0 and head["latest_shard_ref"] is None
    assert not list((ROOT / "state/martial-world/scene-history").glob("*.json"))
    manifest = load("docs/CAMPAIGN_REBASELINE_20260902.json")
    assert manifest["old_revision"] == 158 and manifest["new_revision"] == 1
    assert manifest["fresh_private_recovery_store_required"] is True
    assert (ROOT / manifest["archived_state"]).is_file()

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
