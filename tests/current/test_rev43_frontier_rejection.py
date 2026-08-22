from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

ROOT = Path(__file__).resolve().parents[2]


def test_revision_43_can_continue_to_permanent_team_review():
    repository = RepositoryStore(ROOT)
    meta = repository.read_json("state/meta.json")
    assert meta["revision"] == 43
    assert meta["time"] == "SE-0061-09-13T21:15:00"

    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="regression-rev43-frontier",
        actor_id=meta["player_id"],
        command_type="advance_time",
        expected_revision=43,
        submitted_at="2026-08-22T14:45:00Z",
        payload={"target_time": "SE-0061-09-14T09:15:00"},
        mode="gameplay",
    )
    planner = RepositoryCommandPlanner(repository)
    built = planner._build(command)
    manifest = TransactionPlanner(repository, meta_path="state/meta.json").plan(
        command,
        transaction_id="tx.gameplay." + command.digest,
        created_at=command.submitted_at,
        writes=built.writes,
    )
    overlay = StagedOverlay(repository, manifest)
    if planner.schema_validator is not None:
        planner.schema_validator.validate_overlay(overlay, manifest.paths)
    if planner.template_validator is not None:
        planner.template_validator.validate_overlay(overlay, manifest.paths)
    built.validator(overlay, manifest)
