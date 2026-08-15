from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_CHILD = r'''
from pathlib import Path

from shinobi_runtime.api.campaign_entrypoint import _install_campaign_extensions
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.campaign_environment import CampaignCommandPlanner
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import (
    RegisteredSchemaValidator,
    RegisteredTemplateValidator,
    RepositoryStore,
)
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionPlanner

root = Path.cwd()
repo = RepositoryStore(root)
meta = repo.read_json("state/meta.json")
scheduler = repo.read_json("state/time/causal-scheduler.json")
current = meta["time"]
target = scheduler["next_due"]
assert isinstance(target, str) and target > current, (current, target)

_install_campaign_extensions()
assert getattr(
    AutonomyCommandsMixin._apply_institution_autonomy_review,
    "_institution_review_runtime_guard",
    False,
), "final institution review guard was not installed"
assert getattr(
    RepositoryCommandPlanner._world_event_writes,
    "_institution_review_serialization_guard",
    False,
), "institution review serialization guard was not installed"

command = CommandEnvelope(
    campaign_id=meta["campaign_id"],
    request_id="regression.production.monthly.frontier",
    actor_id=meta["player_id"],
    command_type="advance_time",
    expected_revision=meta["revision"],
    submitted_at="2026-08-16T00:00:00Z",
    payload={"target_time": target},
    mode="gameplay",
)
planner = CampaignCommandPlanner(repo)
plan = planner.plan(command)
manifest = TransactionPlanner(repo).plan(
    command,
    transaction_id=plan.transaction_id,
    created_at=plan.created_at,
    writes=plan.writes,
)
overlay = StagedOverlay(repo, manifest)
RegisteredSchemaValidator(repo).validate_overlay(overlay, manifest.paths)
RegisteredTemplateValidator(repo).validate_overlay(overlay, manifest.paths)
plan.validator(overlay, manifest)

assert plan.result.get("world_registry_reviews"), plan.result
assert plan.result.get("world_time") == target, plan.result

semantic_events = []
for path in manifest.paths:
    if path != "state/reg/world-events.json" and not path.startswith("state/history/events/"):
        continue
    record = overlay.read_json(path)
    rows = record.get("events") if isinstance(record, dict) else None
    if isinstance(rows, list):
        semantic_events.extend(rows)

for event in semantic_events:
    if not isinstance(event, dict) or event.get("kind") != "institution_autonomy_reviewed":
        continue
    refs = event.get("material_consequence_refs")
    assert isinstance(refs, list) and refs, event
'''


def test_real_campaign_next_monthly_frontier_validates_through_production_extensions() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "runtime")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        "production monthly frontier regression failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
