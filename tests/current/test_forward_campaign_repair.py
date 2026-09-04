import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.repair import CampaignRepairService, REPAIR_COMMAND_TYPE
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.tx.git import (
    CAMPAIGN_TRAILER,
    MODE_TRAILER,
    REVISION_TRAILER,
    TRANSACTION_TRAILER,
    GitCommitRecord,
)
from shinobi_runtime.tx.manifest import TransactionPlanner


DAMAGED_TX = "tx.gameplay." + "e" * 64
DAMAGED_COMMIT = "2" * 40
RESTORE_COMMIT = "1" * 40
HEAD = "3" * 40


def _json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


class _Repository:
    def __init__(self, root: Path):
        self.root = root
        self.files = {
            "state/meta.json": _json_bytes({
                "schema": "meta",
                "campaign_id": "test-campaign",
                "game": "jianghu",
                "revision": 146,
                "time": "SE-0061-09-28T05:48:50",
                "player_id": "pc.test",
            }),
            "state/example.json": _json_bytes({"value": "damaged"}),
        }

    def read_optional_bytes(self, path):
        return self.files.get(str(path))

    def read_bytes(self, path):
        value = self.read_optional_bytes(path)
        if value is None:
            raise FileNotFoundError(str(path))
        return value

    def read_json(self, path):
        return json.loads(self.read_bytes(path).decode("utf-8"))

    def digest(self, path):
        value = self.read_optional_bytes(path)
        return None if value is None else hashlib.sha256(value).hexdigest()

    def require_campaign(self, expected, path="state/meta.json"):
        actual = self.read_json(path)["campaign_id"]
        if actual != expected:
            raise ValueError("campaign mismatch")
        return actual

    def require_revision(self, expected, path="state/meta.json"):
        from shinobi_runtime.tx.errors import StaleRevisionError
        actual = self.read_json(path)["revision"]
        if actual != expected:
            raise StaleRevisionError(expected, actual)
        return actual


class _Git:
    def __init__(self):
        self.current_tree = "state-tree-damaged"
        self.damaged = GitCommitRecord(
            commit_hash=DAMAGED_COMMIT,
            paths=("state/example.json", "state/meta.json"),
            trailers={
                TRANSACTION_TRAILER: DAMAGED_TX,
                CAMPAIGN_TRAILER: "test-campaign",
                REVISION_TRAILER: "146",
                MODE_TRAILER: "gameplay",
            },
        )
        self.restore = {
            "state/meta.json": _json_bytes({
                "schema": "meta",
                "campaign_id": "test-campaign",
                "game": "jianghu",
                "revision": 145,
                "time": "SE-0061-09-28T00:53:58",
                "player_id": "pc.test",
            }),
            "state/example.json": _json_bytes({"value": "clean"}),
        }
        self.damaged_files = {
            "state/meta.json": _json_bytes({
                "schema": "meta",
                "campaign_id": "test-campaign",
                "game": "jianghu",
                "revision": 146,
                "time": "SE-0061-09-28T05:48:50",
                "player_id": "pc.test",
            }),
            "state/example.json": _json_bytes({"value": "damaged"}),
        }

    def head(self):
        return HEAD

    def assert_pristine(self):
        return None

    def find_transaction_commit(self, transaction_id):
        return self.damaged if transaction_id == DAMAGED_TX else None

    def is_ancestor(self, ancestor, descendant):
        return ancestor == DAMAGED_COMMIT and descendant == HEAD

    def tree_oid(self, commit, path):
        assert path == "state"
        if commit == HEAD:
            return self.current_tree
        if commit == DAMAGED_COMMIT:
            return "state-tree-damaged"
        raise AssertionError(commit)

    def first_parent(self, commit):
        assert commit == DAMAGED_COMMIT
        return RESTORE_COMMIT

    def read_path_at(self, commit, path):
        if commit == RESTORE_COMMIT:
            return self.restore.get(path)
        if commit == DAMAGED_COMMIT:
            return self.damaged_files.get(path)
        return None


class _Coordinator:
    def __init__(self, git):
        self.git = git


class _Operations:
    def __init__(self, repository, git):
        self.repository = repository
        self.coordinator = _Coordinator(git)
        self.allowed_actor_ids = frozenset({"pc.test"})

    def _locked(self):
        return nullcontext()

    def _read_fingerprint(self):
        return HEAD, "root"

    def _require_read_only(self, before, code):
        assert before == (HEAD, "root")


def _command(payload=None):
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id="repair.rev146.test",
        actor_id="pc.test",
        command_type=REPAIR_COMMAND_TYPE,
        expected_revision=146,
        submitted_at="2026-08-30T22:30:00Z",
        payload={"damaged_transaction_id": DAMAGED_TX} if payload is None else payload,
        mode="repair",
    )


def test_repair_plan_restores_only_damaged_transaction_paths_and_advances_revision(tmp_path):
    repository = _Repository(tmp_path)
    service = CampaignRepairService(_Operations(repository, _Git()))

    plan = service._build(_command())

    assert set(plan.writes) == {"state/example.json", "state/meta.json"}
    assert json.loads(plan.writes["state/example.json"].decode()) == {"value": "clean"}
    repaired_meta = json.loads(plan.writes["state/meta.json"].decode())
    assert repaired_meta["revision"] == 147
    assert repaired_meta["time"] == "SE-0061-09-28T00:53:58"
    assert plan.result["damaged_revision"] == 146
    assert plan.result["restored_state_revision"] == 145
    assert plan.result["committed_revision"] == 147
    assert plan.result["damaged_commit"] == DAMAGED_COMMIT
    assert plan.result["restore_commit"] == RESTORE_COMMIT


def test_repair_fails_closed_if_state_changed_after_damaged_transaction(tmp_path):
    repository = _Repository(tmp_path)
    git = _Git()
    git.current_tree = "later-state-tree"
    service = CampaignRepairService(_Operations(repository, git))

    with pytest.raises(OperationError) as caught:
        service._build(_command())

    assert caught.value.code == "repair_base_changed"


def test_repair_payload_cannot_choose_arbitrary_restore_commit_or_path(tmp_path):
    repository = _Repository(tmp_path)
    service = CampaignRepairService(_Operations(repository, _Git()))
    command = _command({
        "damaged_transaction_id": DAMAGED_TX,
        "restore_commit": "f" * 40,
    })

    with pytest.raises(OperationError) as caught:
        service._build(command)

    assert caught.value.code == "repair_payload_invalid"


def test_transaction_planner_treats_repair_as_forward_revision(tmp_path):
    repository = _Repository(tmp_path)
    planner = TransactionPlanner(repository)
    repaired_meta = repository.read_json("state/meta.json")
    repaired_meta["revision"] = 147
    repaired_meta["time"] = "SE-0061-09-28T00:53:58"

    manifest = planner.plan(
        _command(),
        transaction_id="tx.repair." + "a" * 64,
        created_at="2026-08-30T22:30:00Z",
        writes={"state/meta.json": _json_bytes(repaired_meta)},
    )

    assert manifest.mode == "repair"
    assert manifest.base_revision == 146
    assert manifest.target_revision == 147


def test_mcp_exposes_dedicated_repair_tools_outside_normal_command_catalog():
    source = (Path(__file__).resolve().parents[2] / "runtime/shinobi_runtime/api/mcp.py").read_text(encoding="utf-8")
    assert 'name="preview_campaign_repair"' in source
    assert 'name="execute_campaign_repair"' in source
