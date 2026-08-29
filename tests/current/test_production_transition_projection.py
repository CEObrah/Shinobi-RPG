from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_entrypoint_composes_current_transition_recovery():
    entrypoint = (ROOT / "runtime/shinobi_runtime/api/campaign_entrypoint.py").read_text(encoding="utf-8")
    transition = (ROOT / "runtime/shinobi_runtime/api/transition_operations.py").read_text(encoding="utf-8")

    assert "TransitionAwareCampaignOperations" in entrypoint
    assert "class TransitionAwareCampaignOperations(ParleyAwareCampaignOperations)" in transition
    assert '"transition:current"' in transition
    assert "get_campaign_revision" in transition
    assert "next_object_ref" in transition
