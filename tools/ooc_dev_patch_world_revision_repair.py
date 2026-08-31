from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
repair_path = ROOT / "runtime/shinobi_runtime/api/repair.py"
text = repair_path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one repair match, found {count}: {old[:140]!r}")
    text = text.replace(old, new, 1)


replace_once(
    '''        # A chain repair is intentionally stricter than general ancestry: the\n        # damaged state transactions themselves must be an exact first-parent\n        # sequence. This prevents a caller from skipping an intervening state\n        # mutation or selecting unrelated historical commits. Source-only commits\n        # may exist after the newest damaged transaction because state-tree\n        # equality below proves they did not mutate campaign truth.\n        for older, newer in zip(damaged_records, damaged_records[1:]):\n            if git.first_parent(newer.commit_hash) != older.commit_hash:\n                raise OperationError(409, "repair_provenance_invalid")\n''',
    '''        # World revisions, not source commits, are the repair continuity\n        # authority. Source-only commits may legally sit between two consecutive\n        # campaign transactions. Prove that no state mutation was skipped by\n        # requiring the first parent of each newer world revision to expose the\n        # exact same ``state`` tree as the preceding transaction's after-image.\n        # If any unlisted gameplay/autonomous/repair transaction changed state in\n        # between, the tree OIDs differ and the repair fails closed.\n        for older, newer in zip(damaged_records, damaged_records[1:]):\n            newer_parent = git.first_parent(newer.commit_hash)\n            if git.tree_oid(newer_parent, "state") != git.tree_oid(older.commit_hash, "state"):\n                raise OperationError(409, "repair_provenance_invalid")\n''',
)

replace_once(
    '''                or damaged.trailers.get(MODE_TRAILER) not in {"gameplay", "autonomous"}\n''',
    '''                or damaged.trailers.get(MODE_TRAILER) not in {"gameplay", "autonomous", "repair"}\n''',
)

replace_once(
    '''                else "forward_transaction_chain_repair"\n''',
    '''                else "forward_world_revision_chain_repair"\n''',
)

repair_path.write_text(text, encoding="utf-8")

# Replace the focused chain harness with one that models source-only commits and
# an intermediate repair revision explicitly.
test_path = ROOT / "tests/current/test_forward_campaign_repair_chain.py"
test_path.write_text(r'''from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.repair import CampaignRepairService, REPAIR_COMMAND_TYPE
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.tx.git import (
    CAMPAIGN_TRAILER, MODE_TRAILER, REVISION_TRAILER, TRANSACTION_TRAILER, GitCommitRecord,
)


def _json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _meta(revision: int, time: str):
    return _json_bytes({
        "schema": "meta", "campaign_id": "test-campaign", "game": "jianghu",
        "revision": revision, "time": time, "player_id": "pc.test",
    })


TX148 = "tx.gameplay." + "a" * 64
TX149 = "tx.repair." + "b" * 64
TX150 = "tx.gameplay." + "c" * 64
BASE147 = "1" * 40
COMMIT148 = "2" * 40
SOURCE_AFTER_148 = "a" * 40
COMMIT149 = "3" * 40
SOURCE_AFTER_149 = "b" * 40
COMMIT150 = "4" * 40
HEAD = "5" * 40


class _Repository:
    def __init__(self, root: Path):
        self.root = root
        self.files = {
            "state/meta.json": _meta(150, "SE-0061-09-28T01:02:18"),
            "state/a.json": _json_bytes({"value": "damaged-148"}),
            "state/b.json": _json_bytes({"value": "damaged-149"}),
            "state/c.json": _json_bytes({"value": "damaged-150"}),
            "state/untouched.json": _json_bytes({"value": "keep"}),
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
    def __init__(self, *, skipped_state_mutation: bool = False):
        self.records = {
            TX148: GitCommitRecord(
                commit_hash=COMMIT148, paths=("state/a.json", "state/meta.json"),
                trailers={TRANSACTION_TRAILER: TX148, CAMPAIGN_TRAILER: "test-campaign", REVISION_TRAILER: "148", MODE_TRAILER: "gameplay"},
            ),
            TX149: GitCommitRecord(
                commit_hash=COMMIT149, paths=("state/b.json", "state/meta.json"),
                trailers={TRANSACTION_TRAILER: TX149, CAMPAIGN_TRAILER: "test-campaign", REVISION_TRAILER: "149", MODE_TRAILER: "repair"},
            ),
            TX150: GitCommitRecord(
                commit_hash=COMMIT150, paths=("state/c.json", "state/meta.json"),
                trailers={TRANSACTION_TRAILER: TX150, CAMPAIGN_TRAILER: "test-campaign", REVISION_TRAILER: "150", MODE_TRAILER: "gameplay"},
            ),
        }
        # Source commits sit between world revisions without changing state.
        self.parents = {
            COMMIT148: BASE147,
            COMMIT149: SOURCE_AFTER_148,
            COMMIT150: SOURCE_AFTER_149,
        }
        self.state_trees = {
            BASE147: "tree-147",
            COMMIT148: "tree-148",
            SOURCE_AFTER_148: "tree-skipped" if skipped_state_mutation else "tree-148",
            COMMIT149: "tree-149",
            SOURCE_AFTER_149: "tree-149",
            COMMIT150: "tree-150",
            HEAD: "tree-150",
        }
        self.files = {
            BASE147: {
                "state/meta.json": _meta(147, "SE-0061-09-28T00:53:58"),
                "state/a.json": _json_bytes({"value": "clean-a"}),
                "state/b.json": _json_bytes({"value": "clean-b"}),
                "state/c.json": _json_bytes({"value": "clean-c"}),
            },
            COMMIT148: {"state/meta.json": _meta(148, "t148")},
            COMMIT149: {"state/meta.json": _meta(149, "t149")},
            COMMIT150: {"state/meta.json": _meta(150, "t150")},
        }

    def head(self):
        return HEAD

    def assert_pristine(self):
        return None

    def find_transaction_commit(self, transaction_id):
        return self.records.get(transaction_id)

    def is_ancestor(self, ancestor, descendant):
        return descendant == HEAD and ancestor in {COMMIT148, COMMIT149, COMMIT150}

    def tree_oid(self, commit, path):
        assert path == "state"
        return self.state_trees[commit]

    def first_parent(self, commit):
        return self.parents[commit]

    def read_path_at(self, commit, path):
        return self.files.get(commit, {}).get(path)


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


def _command(ids=None):
    if ids is None:
        ids = [TX148, TX149, TX150]
    return CommandEnvelope(
        campaign_id="test-campaign", request_id="repair.rev148-150.test", actor_id="pc.test",
        command_type=REPAIR_COMMAND_TYPE, expected_revision=150, submitted_at="2026-08-30T23:30:00Z",
        payload={"damaged_transaction_ids": ids}, mode="repair",
    )


def test_world_revision_chain_allows_source_commits_and_repair_revision(tmp_path):
    repository = _Repository(tmp_path)
    service = CampaignRepairService(_Operations(repository, _Git()))

    plan = service._build(_command())

    assert set(plan.writes) == {"state/a.json", "state/b.json", "state/c.json", "state/meta.json"}
    assert json.loads(plan.writes["state/a.json"].decode()) == {"value": "clean-a"}
    assert json.loads(plan.writes["state/b.json"].decode()) == {"value": "clean-b"}
    assert json.loads(plan.writes["state/c.json"].decode()) == {"value": "clean-c"}
    repaired_meta = json.loads(plan.writes["state/meta.json"].decode())
    assert repaired_meta["revision"] == 151
    assert repaired_meta["time"] == "SE-0061-09-28T00:53:58"
    assert plan.result["repair_kind"] == "forward_world_revision_chain_repair"
    assert plan.result["damaged_revision_start"] == 148
    assert plan.result["damaged_revision_end"] == 150
    assert plan.result["restored_state_revision"] == 147
    assert plan.result["damaged_transaction_ids"] == [TX148, TX149, TX150]
    assert plan.result["restore_commit"] == BASE147
    assert "state/untouched.json" not in plan.writes


def test_world_revision_chain_rejects_an_unlisted_intervening_state_change(tmp_path):
    service = CampaignRepairService(_Operations(_Repository(tmp_path), _Git(skipped_state_mutation=True)))
    with pytest.raises(OperationError) as caught:
        service._build(_command())
    assert caught.value.code == "repair_provenance_invalid"


def test_chain_repair_rejects_skipping_an_intermediate_revision(tmp_path):
    service = CampaignRepairService(_Operations(_Repository(tmp_path), _Git()))
    with pytest.raises(OperationError) as caught:
        service._build(_command([TX148, TX150]))
    assert caught.value.code == "repair_provenance_invalid"


def test_chain_repair_rejects_wrong_order_even_when_all_ids_are_real(tmp_path):
    service = CampaignRepairService(_Operations(_Repository(tmp_path), _Git()))
    with pytest.raises(OperationError) as caught:
        service._build(_command([TX149, TX148, TX150]))
    assert caught.value.code == "repair_provenance_invalid"


def test_chain_payload_rejects_duplicates_and_is_bounded(tmp_path):
    service = CampaignRepairService(_Operations(_Repository(tmp_path), _Git()))
    with pytest.raises(OperationError) as caught:
        service._build(_command([TX148, TX148, TX150]))
    assert caught.value.code == "repair_payload_invalid"

    too_many = ["tx.gameplay." + f"{index:064x}" for index in range(33)]
    with pytest.raises(OperationError) as caught:
        service._build(_command(too_many))
    assert caught.value.code == "repair_payload_invalid"
''', encoding="utf-8")

print("world-revision chain repair staged")
