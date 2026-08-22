from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store.repository import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def test_current_sep13_monthly_frontier_previews_cleanly():
    repository = RepositoryStore(ROOT)
    planner = RepositoryCommandPlanner(repository)
    meta = repository.read_json("state/meta.json")
    assert meta["time"] == "SE-0061-09-12T21:15:00"
    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="request.sep13-monthly-frontier-regression",
        actor_id=meta["player_id"],
        command_type="advance_time",
        expected_revision=meta["revision"],
        submitted_at="2026-08-22T02:20:00Z",
        payload={"target_time": "SE-0061-09-13T21:15:00"},
        mode="gameplay",
    )
    preview = planner.preview(command)
    assert preview.status == "ready"
    assert preview.target_revision == meta["revision"] + 1
