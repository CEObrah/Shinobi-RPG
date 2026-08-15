"""Long-horizon integration smoke over a disposable real-campaign clone.

The test deliberately starts from the shipped campaign state and uses the
production campaign planner plus the real transaction coordinator. Player-only
interrupts are suppressed only inside the disposable clone after they are
lawfully reached; this test is an autonomous-system stress harness, not an
alternate campaign authority.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.campaign_runtime_planner import CampaignCommandPlanner
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler_store import SchedulerStore
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.tx import GitStager, ReceiptStore, TransactionCoordinator, WriteAheadLog


ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ID = "shinobi-wei-main"
DAY_SECONDS = 24 * 60 * 60
MAX_HORIZON_DAYS = 90


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _copy_campaign(tmp_path: Path, name: str) -> tuple[Path, Path]:
    root = tmp_path / name
    runtime_root = tmp_path / f"{name}-runtime"
    shutil.copytree(
        ROOT,
        root,
        ignore=shutil.ignore_patterns(
            ".git", ".pytest_cache", "__pycache__", "*.pyc", "*.egg-info"
        ),
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "stability@example.invalid")
    _git(root, "config", "user.name", "Stability Harness")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "stability baseline")
    return root, runtime_root


def _state_digest(root: Path) -> str:
    digest = hashlib.sha256()
    state = root / "state"
    for path in sorted(item for item in state.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _suppress_reached_player_boundary(root: Path) -> bool:
    """Reopen only player-interrupt flags in the disposable smoke clone."""

    scene_path = root / "state/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    repository = RepositoryStore(root)
    store = SchedulerStore(repository)
    scheduler = store.load(full=True)
    changed = False
    events = []
    for event in scheduler.queue.snapshot():
        if event.requires_player is True:
            event = replace(event, requires_player=False)
            changed = True
        events.append(event)
    scheduler.queue.replace(events)
    if scene.get("time_passage_allowed") is False:
        scene["time_passage_allowed"] = True
        changed = True
    if scene.get("known_clock_boundaries"):
        scene["known_clock_boundaries"] = []
        changed = True
    if not changed:
        return False
    scene_path.write_text(json.dumps(scene, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for relative, content in store.write_images(scheduler).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _git(root, "add", "state/scene.json", "state/time/causal-scheduler.json", "state/time/causal-scheduler")
    _git(root, "commit", "-qm", "stability harness: suppress reached player boundary")
    return True


def _simulate(root: Path, runtime_root: Path, horizon_days: int) -> tuple[str, int, int]:
    repository = RepositoryStore(root)
    stager = GitStager(root)
    coordinator = TransactionCoordinator(
        repository,
        stager,
        WriteAheadLog(runtime_root / "wal"),
        ReceiptStore(runtime_root / "receipts"),
        lock_path=runtime_root / "writer.lock",
    )
    planner = CampaignCommandPlanner(repository)
    schema_validator = RegisteredSchemaValidator(repository)
    template_validator = RegisteredTemplateValidator(repository)

    start = CampaignTime.parse(repository.read_json("state/meta.json")["time"])
    goal = start.add_seconds(horizon_days * DAY_SECONDS)
    commits = 0
    player_boundaries = 0
    sequence = 0

    while CampaignTime.parse(repository.read_json("state/meta.json")["time"]) < goal:
        meta = repository.read_json("state/meta.json")
        current = CampaignTime.parse(meta["time"])
        target = min(current.add_seconds(DAY_SECONDS), goal)
        sequence += 1
        command = CommandEnvelope(
            campaign_id=CAMPAIGN_ID,
            request_id=f"stability-{sequence:04d}-{current}",
            actor_id=meta["player_id"],
            command_type="advance_time",
            expected_revision=meta["revision"],
            submitted_at="2026-08-11T05:45:00Z",
            payload={"target_time": str(target)},
            mode="gameplay",
        )
        plan = planner.plan(command)

        def validate(overlay, manifest) -> None:
            schema_validator.validate_overlay(overlay, manifest.paths)
            template_validator.validate_overlay(overlay, manifest.paths)
            plan.validator(overlay, manifest)

        execution = coordinator.execute(
            command,
            transaction_id=plan.transaction_id,
            created_at=plan.created_at,
            writes=plan.writes,
            result=dict(plan.result),
            validator=validate,
        )
        assert execution.status == "committed"
        commits += 1
        after = repository.read_json("state/meta.json")
        assert after["revision"] == meta["revision"] + 1
        after_time = CampaignTime.parse(after["time"])
        assert current < after_time <= target
        stager.assert_pristine()

        if plan.result.get("interrupted"):
            player_boundaries += 1
            assert _suppress_reached_player_boundary(root), (
                "autonomous smoke reached a non-suppressible player interrupt"
            )
            stager.assert_pristine()

        if sequence > horizon_days * 8 + 128:
            raise AssertionError("stability smoke made insufficient temporal progress")

    return _state_digest(root), commits, player_boundaries


def test_real_campaign_survives_long_horizon_deterministically(tmp_path: Path) -> None:
    raw_horizon = os.environ.get("STABILITY_HORIZON_DAYS")
    if raw_horizon is None:
        pytest.skip("long-horizon replay runs only from the explicit stability CI gate")
    horizon = int(raw_horizon)
    assert 1 <= horizon <= MAX_HORIZON_DAYS
    first_root, first_runtime = _copy_campaign(tmp_path, "first")
    second_root, second_runtime = _copy_campaign(tmp_path, "second")

    first = _simulate(first_root, first_runtime, horizon)
    second = _simulate(second_root, second_runtime, horizon)

    assert first == second
    _digest, commits, _player_boundaries = first
    assert commits >= horizon
