from __future__ import annotations

from pathlib import Path

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.campaign_planner import CampaignCommandPlanner
from shinobi_runtime.store import RepositoryStore


def test_current_long_travel_can_preview_past_next_quiet_frontier():
    """Regression for the rev-130 Huashan muster progression blocker.

    This deliberately uses the checked-in campaign snapshot because the defect
    appeared only after a long chain of real causal settlements. Preview is
    read-only, so the fixture remains authoritative and unchanged.
    """
    root = Path(__file__).resolve().parents[2]
    repository = RepositoryStore(root)
    meta = repository.read_json("state/meta.json")
    assert meta["campaign_id"] == "jianghu-wei-main"
    assert meta["revision"] == 130
    assert meta["time"] == "SE-0061-09-26T21:15:00"

    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="regression-long-travel-r130",
        actor_id=meta["player_id"],
        command_type="advance_time",
        expected_revision=meta["revision"],
        submitted_at="2026-08-28T12:00:00Z",
        mode="gameplay",
        payload={"target_time": "SE-0061-10-01T11:15:00"},
    )

    preview = CampaignCommandPlanner(repository).preview(command)
    assert preview.status == "ready"
    assert preview.target_revision == 131
