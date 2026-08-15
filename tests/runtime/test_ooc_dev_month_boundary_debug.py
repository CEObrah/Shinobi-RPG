"""Temporary OOC DEV regression for the real campaign monthly wait boundary.

This file is intentionally snapshot-specific while diagnosing the production
planner. It must be replaced by a durable regression before merge.
"""
from pathlib import Path

from shinobi_runtime.api.campaign_entrypoint import _install_campaign_extensions
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.campaign_environment import CampaignCommandPlanner
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ID = "shinobi-wei-main"
ACTOR = "pc_wei_tang"


def test_real_event_seek_crosses_next_monthly_boundary_without_raw_exception() -> None:
    _install_campaign_extensions()
    repository = RepositoryStore(ROOT)
    meta = repository.read_json("state/meta.json")
    current = CampaignTime.parse(meta["time"])
    assert str(current) == "SE-0061-06-30T07:00:00"
    command = CommandEnvelope(
        campaign_id=CAMPAIGN_ID,
        request_id="ooc-dev-month-boundary-debug",
        actor_id=ACTOR,
        command_type="advance_until_event",
        expected_revision=meta["revision"],
        submitted_at="2026-08-16T00:00:00Z",
        payload={"target_time": "SE-0061-07-05T10:00:00"},
        mode="gameplay",
    )
    plan = CampaignCommandPlanner(repository).plan(command)
    assert plan.result["advance_until_event"]["boundary_target"] >= "SE-0061-07-01T07:00:00"
