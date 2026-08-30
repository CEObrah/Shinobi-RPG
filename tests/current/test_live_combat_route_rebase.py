import shutil
from pathlib import Path

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _copy_live_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "runtime/contracts", root / "runtime/contracts")
    return root


def test_live_contact_pending_combat_bare_exchange_previews_without_route_progress(tmp_path):
    root = _copy_live_repository(tmp_path)
    repo = RepositoryStore(root)
    planner = RepositoryCommandPlanner(repo)
    meta = repo.read_json("state/meta.json")
    player_ref = str(meta["player_id"])

    combat_state = repo.read_json("state/martial-world/combats.json")
    combats = combat_state.get("combats", {})
    combat_ref = next(
        ref
        for ref, combat in combats.items()
        if isinstance(combat, dict)
        and combat.get("status") == "active"
        and any(
            player_ref in members
            for members in combat.get("sides", {}).values()
            if isinstance(members, list)
        )
    )

    route_state = repo.read_json("state/martial-world/route-operations.json")
    movements = route_state.get("movements", {})
    movement_ref, movement_before = next(
        (ref, row)
        for ref, row in movements.items()
        if isinstance(row, dict)
        and row.get("status") == "contact_pending"
        and row.get("combat_ref") == combat_ref
        and player_ref in row.get("participant_refs", [])
    )

    command = CommandEnvelope(
        campaign_id=meta["campaign_id"],
        request_id="test.live-route-combat.exchange",
        actor_id=player_ref,
        command_type="jianghu_combat_resolution",
        expected_revision=meta["revision"],
        submitted_at="2026-08-30T00:00:00Z",
        payload={"action": "exchange", "combat_ref": combat_ref},
        mode="gameplay",
    )

    try:
        preview = planner.preview(command)
    except CommandRejectedError as exc:
        cause = exc.__cause__
        pytest.fail(f"live combat preview rejected: {exc}; wrapped cause: {cause!r}")
    assert preview.status == "ready"
    plan = planner.plan(command)

    route_after = movement_before
    raw_route_after = plan.writes.get("state/martial-world/route-operations.json")
    if raw_route_after is not None:
        import json
        route_after = json.loads(raw_route_after.decode("utf-8"))["movements"][movement_ref]

    assert route_after["status"] == "contact_pending"
    assert route_after.get("combat_ref") == combat_ref
    assert route_after.get("elapsed_seconds") == movement_before.get("elapsed_seconds")
    assert route_after.get("last_progress_at") == movement_before.get("last_progress_at")
    assert plan.result["world_time"] > meta["time"]
    assert plan.result["events"]
