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
    # This fixture tests custody escape, not the canonical campaign's current
    # Black Lance fight. Remove that unrelated exact-combat owner so combat
    # gating cannot mask the custody rule under test.
    combats = repo.read_json("state/martial-world/combats.json")
    for row in combats.get("combats", {}).values():
        if not isinstance(row, dict) or row.get("status") != "active":
            continue
        sides = row.get("sides", {}) if isinstance(row.get("sides"), dict) else {}
        members = {str(ref) for refs in sides.values() if isinstance(refs, list) for ref in refs if isinstance(ref, str)}
        if meta["player_id"] in members:
            row["status"] = "resolved"
    _replace_json(repo, "state/martial-world/combats.json", combats)
    scene = repo.read_json("state/scene.json")
    if isinstance(scene, dict):
        scene.pop("active_combat_ref", None)
        _replace_json(repo, "state/scene.json", scene)


def test_faction_custody_escape_cannot_ignore_other_usable_local_guards(tmp_path):
    repo=_copy_repo(tmp_path); _seed_faction_custody(repo)
    planner=RepositoryCommandPlanner(repo)
    # Canonical scene already contains three other healthy House Tang people.
    with pytest.raises(CommandRejectedError, match="jianghu_custody_escape_requires_physical_opening"):
        planner.plan(_escape_command(repo,"test.custody.guarded"))


def test_faction_custody_escape_can_use_real_opening_when_no_custodian_guard_remains(tmp_path):
    repo=_copy_repo(tmp_path); _seed_faction_custody(repo)
    player=repo.read_json("state/meta.json")["player_id"]
    roster=repo.read_json("state/martial-world/people/house_tang.json")
    # Scene projection is presentation-only. Create a real physical opening by
    # making every other exact House Tang custodian unusable in this fixture.
    for person in roster.get("people", []):
        if not isinstance(person, dict) or person.get("person_id") == player:
            continue
        person["health"]={"status":"dead","consciousness":0,"shock":0,"injuries":[]}
    _replace_json(repo,"state/martial-world/people/house_tang.json",roster)
    planner=RepositoryCommandPlanner(repo)
    plan=planner.plan(_escape_command(repo,"test.custody.open"))
    assert plan.result["custody"]["status"] == "escaped"


def test_duplicate_active_custody_is_rejected_as_physical_authority_ambiguity():
    from shinobi_runtime.martial_world.captivity_lifecycle import validate_active_custody_uniqueness

    state = {
        "records": [
            {"custody_id": "custody:a", "person_ref": "person.a", "status": "restrained"},
            {"custody_id": "custody:b", "person_ref": "person.a", "status": "captive"},
        ]
    }
    with pytest.raises(ValueError, match="duplicate active custody"):
        validate_active_custody_uniqueness(state)


def test_terminal_executed_custody_is_not_selected_as_live_escape_owner(tmp_path):
    repo = _copy_repo(tmp_path)
    meta = repo.read_json("state/meta.json")
    player = meta["player_id"]
    custody = {
        "schema": "jianghu-custody-state-1.0",
        "records": [{
            "custody_id": "custody:test:executed",
            "person_ref": player,
            "captor_ref": "missing.captor",
            "status": "executed",
            "location_ref": "site.house_tang",
            "basis": "test_terminal_record",
            "started_at": "0061-09-14T09:00:00",
        }],
    }
    _replace_json(repo, "state/martial-world/custody.json", custody)
    combats = repo.read_json("state/martial-world/combats.json")
    for row in combats.get("combats", {}).values():
        if isinstance(row, dict) and row.get("status") == "active":
            row["status"] = "resolved"
    _replace_json(repo, "state/martial-world/combats.json", combats)

    planner = RepositoryCommandPlanner(repo)
    with pytest.raises(CommandRejectedError, match="jianghu_custody_record_not_found"):
        planner.plan(_escape_command(repo, "test.custody.executed-terminal"))
