import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shinobi_runtime.api.historical_repair_anchor import (
    HISTORICAL_COMBAT_ANCHOR,
    build_historical_anchor_repair,
)
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.tx.git import (
    CAMPAIGN_TRAILER,
    COMMAND_DIGEST_TRAILER,
    MODE_TRAILER,
    REQUEST_TRAILER,
    REVISION_TRAILER,
    TRANSACTION_TRAILER,
    GitCommitRecord,
)

BAD = "df686d903b2ed526030ebffcd7997040968725ae"
BASE = "63556b9cbcfcd96bbb0f938fc5ce31f41a3fa92a"
ROOT = "47fcd196a87c301daf7566d92edf317604ca15bc"
TX = "tx.gameplay.40dd7acfe68c326566ff6130e271e949897253cf662eb713005dcfcd768d0be4"
STATE_TREE = "a8ec71acec5d0ca8c129f0bae70823d0a7445659"


def raw(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def digest(value):
    return None if value is None else hashlib.sha256(value).hexdigest()


def image(value):
    return None if value is None else base64.b64encode(value).decode("ascii")


def meta(rev, time):
    return raw({
        "schema": "meta",
        "campaign_id": "jianghu-wei-main",
        "game": "jianghu",
        "revision": rev,
        "time": time,
        "player_id": "pc_wei_tang",
    })


def combats(elapsed):
    return raw({
        "schema": "jianghu-combat-state-1.0",
        "combats": {
            "combat:contact:escort_muster:0e52cfa45f5bbea72ba0:0061-09-27:black_lance_company": {
                "status": "active",
                "elapsed_ms": elapsed,
            }
        },
    })


def people(fatigue):
    row = {"person_id": "pc_wei_tang"}
    if fatigue:
        row["fatigue_milli"] = fatigue
    return raw({
        "schema": "jianghu-person-lite-roster-1.0",
        "faction_ref": "house_tang",
        "people": [row],
    })


class Repo:
    def __init__(self, files):
        self.files = dict(files)

    def read_optional_bytes(self, path):
        return self.files.get(path)

    def digest(self, path):
        return digest(self.files.get(path))


class Git:
    def __init__(self, files, head="head"):
        self.files = dict(files)
        self._head = head
        self.fetched = []
        self.available = {BAD, BASE, ROOT, head}
        self.release_tree = STATE_TREE

    def _run_bytes(self, args):
        if args[:2] == ("cat-file", "-e"):
            commit = args[2].split("^", 1)[0]
            return SimpleNamespace(returncode=0 if commit in self.available else 1, stdout=b"", stderr=b"")
        if args and args[0] == "fetch":
            self.fetched.append(tuple(args))
            self.available.update({BAD, BASE})
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if args and args[0] == "ls-tree":
            commit = args[4]
            rows = sorted(path for (sha, path) in self.files if sha == commit and path.startswith("state/"))
            return SimpleNamespace(returncode=0, stdout=(b"\x00".join(row.encode() for row in rows) + (b"\x00" if rows else b"")), stderr=b"")
        raise AssertionError(args)

    def root_commits(self):
        return (ROOT,)

    def head(self):
        return self._head

    def get_commit(self, sha):
        assert sha == BAD
        return GitCommitRecord(
            BAD,
            (
                "state/meta.json",
                "state/martial-world/combats.json",
                "state/martial-world/people/house_tang.json",
                "state/example.json",
            ),
            {
                TRANSACTION_TRAILER: TX,
                CAMPAIGN_TRAILER: "jianghu-wei-main",
                REVISION_TRAILER: "143",
                MODE_TRAILER: "gameplay",
                REQUEST_TRAILER: "play.combat.resume.attack.r142",
                COMMAND_DIGEST_TRAILER: "40dd7acfe68c326566ff6130e271e949897253cf662eb713005dcfcd768d0be4",
            },
        )

    def first_parent(self, sha):
        assert sha == BAD
        return BASE

    def tree_oid(self, sha, path):
        assert path == "state"
        if sha == BAD:
            return STATE_TREE
        if sha == ROOT:
            return self.release_tree
        raise AssertionError(sha)

    def read_path_at(self, sha, path):
        return self.files.get((sha, path))


class Wal:
    def __init__(self, records):
        self._records = list(records)

    def records(self, statuses):
        assert tuple(statuses) == ("committed",)
        return tuple(self._records)


class Coordinator:
    def __init__(self, git, wal):
        self.git = git
        self.wal = wal


def wal_record(revision, before_files, after_files, tx):
    entries = []
    for path in sorted(set(before_files) | set(after_files)):
        before = before_files.get(path)
        after = after_files.get(path)
        if before == after:
            continue
        entries.append({
            "path": path,
            "before_sha256": digest(before),
            "after_sha256": digest(after),
            "before_b64": image(before),
            "after_b64": image(after),
        })
    return {
        "transaction_id": tx,
        "manifest": {
            "transaction_id": tx,
            "campaign_id": "jianghu-wei-main",
            "base_revision": revision - 1,
            "target_revision": revision,
            "mode": "gameplay",
        },
        "entries": entries,
    }


def fixture():
    base = {
        "state/meta.json": meta(142, "SE-0061-09-27T21:15:00"),
        "state/martial-world/combats.json": combats(0),
        "state/martial-world/people/house_tang.json": people(0),
        "state/example.json": raw({"v": "base"}),
    }
    root = {
        "state/meta.json": meta(143, "SE-0061-09-27T22:58:33"),
        "state/martial-world/combats.json": combats(6212079),
        "state/martial-world/people/house_tang.json": people(3265),
        "state/example.json": raw({"v": "bad"}),
    }
    r144 = dict(root)
    r144["state/meta.json"] = meta(144, "SE-0061-09-27T22:59:22")
    r144["state/example.json"] = raw({"v": "144"})
    r145 = dict(r144)
    r145["state/meta.json"] = meta(145, "SE-0061-09-28T00:53:58")
    r145["state/example.json"] = raw({"v": "145"})

    files = {}
    for sha, rows in ((BASE, base), (BAD, root), (ROOT, root), ("head", r145)):
        for path, value in rows.items():
            files[(sha, path)] = value
    git = Git(files)
    wal = Wal([
        wal_record(144, root, r144, "tx.gameplay." + "1" * 64),
        wal_record(145, r144, r145, "tx.gameplay." + "2" * 64),
    ])
    return base, Repo(r145), Coordinator(git, wal)


def test_historical_anchor_restores_exact_rev142_state_as_forward_revision():
    base, repo, coordinator = fixture()
    build = build_historical_anchor_repair(
        anchor=HISTORICAL_COMBAT_ANCHOR,
        campaign_id="jianghu-wei-main",
        expected_revision=145,
        repository=repo,
        coordinator=coordinator,
    )

    assert build.result["restored_state_revision"] == 142
    assert build.result["committed_revision"] == 146
    assert build.result["restored_world_time"] == "SE-0061-09-27T21:15:00"
    assert build.result["restored_combat_elapsed_ms"] == 0
    assert build.result["provenance_source"] == "trusted_pre_root_git_anchor_plus_committed_wal"
    repaired_meta = json.loads(build.writes["state/meta.json"])
    assert repaired_meta["revision"] == 146
    assert repaired_meta["time"] == "SE-0061-09-27T21:15:00"
    assert build.writes["state/martial-world/combats.json"] == base["state/martial-world/combats.json"]
    assert build.writes["state/martial-world/people/house_tang.json"] == base["state/martial-world/people/house_tang.json"]


def test_historical_anchor_fails_closed_on_missing_wal_or_current_drift():
    _, repo, coordinator = fixture()
    coordinator.wal._records = coordinator.wal._records[1:]
    with pytest.raises(OperationError) as caught:
        build_historical_anchor_repair(
            anchor=HISTORICAL_COMBAT_ANCHOR,
            campaign_id="jianghu-wei-main",
            expected_revision=145,
            repository=repo,
            coordinator=coordinator,
        )
    assert caught.value.code == "repair_historical_wal_incomplete"

    _, repo, coordinator = fixture()
    repo.files["state/example.json"] = raw({"v": "tampered"})
    with pytest.raises(OperationError) as caught:
        build_historical_anchor_repair(
            anchor=HISTORICAL_COMBAT_ANCHOR,
            campaign_id="jianghu-wei-main",
            expected_revision=145,
            repository=repo,
            coordinator=coordinator,
        )
    assert caught.value.code == "repair_historical_current_state_mismatch"


def test_historical_anchor_requires_exact_release_root_state_tree():
    _, repo, coordinator = fixture()
    coordinator.git.release_tree = "0" * 40
    with pytest.raises(OperationError) as caught:
        build_historical_anchor_repair(
            anchor=HISTORICAL_COMBAT_ANCHOR,
            campaign_id="jianghu-wei-main",
            expected_revision=145,
            repository=repo,
            coordinator=coordinator,
        )
    assert caught.value.code == "repair_historical_release_snapshot_mismatch"


def test_production_entrypoint_installs_closed_historical_repair_composition():
    text = (
        Path(__file__).resolve().parents[2]
        / "runtime/shinobi_runtime/api/campaign_entrypoint.py"
    ).read_text(encoding="utf-8")
    assert "install_historical_repair_anchor" in text
    assert "historical_anchor" not in (
        Path(__file__).resolve().parents[2]
        / "runtime/shinobi_runtime/api/repair.py"
    ).read_text(encoding="utf-8")
