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
    combats = repo.read_json("state/martial-world/combats.json").get("combats", {})
    combat_ref = next(ref for ref, combat in combats.items() if isinstance(combat, dict) and combat.get("status") == "active" and any(player_ref in members for members in combat.get("sides", {}).values() if isinstance(members, list)))
    command = CommandEnvelope(campaign_id=meta["campaign_id"], request_id="test.live-route-combat.exchange", actor_id=player_ref, command_type="jianghu_combat_resolution", expected_revision=meta["revision"], submitted_at="2026-08-30T00:00:00Z", payload={"action": "exchange", "combat_ref": combat_ref}, mode="gameplay")
    try:
        preview = planner.preview(command)
    except CommandRejectedError as exc:
        cause = exc.__cause__
        assert cause is not None
        assert cause.__class__.__name__ == "TemplateValidationError"
        assert getattr(cause, "schema_id", None) == "jianghu-combat-state-1.0"
        return
    pytest.fail(f"diagnostic expected current live preview rejection, got {preview.status}")
