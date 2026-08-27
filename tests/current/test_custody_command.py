import json
import shutil
from pathlib import Path

import pytest

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import RepositoryStore

ROOT = Path(__file__).resolve().parents[2]


def _copy_repo(tmp_path: Path) -> RepositoryStore:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "state", root / "state")
    shutil.copytree(ROOT / "game", root / "game")
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "runtime/contracts", root / "runtime/contracts")
    return RepositoryStore(root)


def _replace_json(repo: RepositoryStore, path: str, value) -> None:
    repo.replace_image(path, (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))


def _escape_command(repo: RepositoryStore, request_id: str) -> CommandEnvelope:
    meta = repo.read_json("state/meta.json")
    return CommandEnvelope(
        campaign_id=meta["campaign_id"], request_id=request_id, actor_id=meta["player_id"],
        command_type="jianghu_custody_resolution", expected_revision=meta["revision"],
        submitted_at="2026-08-27T00:10:00Z",
        payload={"action":"escape_attempt", "person_ref":meta["player_id"]}, mode="gameplay",
    )


def _seed_faction_custody(repo: RepositoryStore) -> None:
    meta=repo.read_json("state/meta.json")
    custody={
        "schema":"jianghu-custody-state-1.0",
        "records":[{
            "custody_id":"custody:test:wei", "person_ref":meta["player_id"],
            "captor_ref":"missing.captor", "holder_faction_ref":"house_tang",
            "status":"restrained", "location_ref":"site.house_tang",
            "basis":"test_faction_custody", "started_at":"0061-09-14T09:00:00",
        }],
    }
    _replace_json(repo,"state/martial-world/custody.json",custody)


def test_faction_custody_escape_cannot_ignore_other_usable_local_guards(tmp_path):
    repo=_copy_repo(tmp_path); _seed_faction_custody(repo)
    planner=RepositoryCommandPlanner(repo)
    # Canonical scene already contains three other healthy House Tang people.
    with pytest.raises(CommandRejectedError, match="jianghu_custody_escape_requires_physical_opening"):
        planner.plan(_escape_command(repo,"test.custody.guarded"))


def test_faction_custody_escape_can_use_real_opening_when_no_custodian_guard_remains(tmp_path):
    repo=_copy_repo(tmp_path); _seed_faction_custody(repo)
    scene=repo.read_json("state/scene.json")
    player=repo.read_json("state/meta.json")["player_id"]
    scene["present_person_ids"]=[player]; scene["visible_person_ids"]=[player]
    _replace_json(repo,"state/scene.json",scene)
    planner=RepositoryCommandPlanner(repo)
    plan=planner.plan(_escape_command(repo,"test.custody.open"))
    assert plan.result["custody"]["status"] == "escaped"
