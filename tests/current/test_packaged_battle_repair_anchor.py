import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.packaged_battle_repair_anchor import (
    PACKAGED_BATTLE_REPAIR_ANCHOR,
    build_packaged_battle_repair,
)
from shinobi_runtime.tx.git import (
    CAMPAIGN_TRAILER,
    MODE_TRAILER,
    REVISION_TRAILER,
    TRANSACTION_TRAILER,
    GitCommitRecord,
)

ROOT = "4d33f3819f7d9d29619fe87df2b00291a16fffcd"
ROOT_TREE = "701ee943517179d8216d4aec8841c999e4a87d20"
BAD = "df686d903b2ed526030ebffcd7997040968725ae"
BASE = "63556b9cbcfcd96bbb0f938fc5ce31f41a3fa92a"
OLD_BAD_TREE = "a8ec71acec5d0ca8c129f0bae70823d0a7445659"
COMBAT = "combat:contact:escort_muster:0e52cfa45f5bbea72ba0:0061-09-27:black_lance_company"
MOVE = "escort_muster:0e52cfa45f5bbea72ba0"


def raw(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def digest(value):
    return None if value is None else hashlib.sha256(value).hexdigest()


def meta(revision, time):
    return raw({
        "schema": "meta",
        "campaign_id": "jianghu-wei-main",
        "game": "jianghu",
        "revision": revision,
        "time": time,
        "player_id": "pc_wei_tang",
    })


def combat(status, elapsed, winner=None):
    row = {"status": status, "elapsed_ms": elapsed}
    if winner is not None:
        row["winner_side"] = winner
    return raw({"schema": "jianghu-combat-state-1.0", "combats": {COMBAT: row}})


def people(fatigue=0):
    row = {"person_id": "pc_wei_tang"}
    if fatigue:
        row["fatigue_milli"] = fatigue
    return raw({"schema": "jianghu-person-lite-roster-1.0", "faction_ref": "house_tang", "people": [row]})


def route(status):
    return raw({
        "schema": "jianghu-route-operations-1.0",
        "movements": {MOVE: {"status": status, "combat_ref": COMBAT}},
        "contacts": {},
    })


class Repo:
    def __init__(self, files):
        self.files = dict(files)

    def read_optional_bytes(self, path):
        return self.files.get(path)

    def digest(self, path):
        return digest(self.files.get(path))


class Git:
    def __init__(self, root_files, restore_files, current_files):
        self.root_files = root_files
        self.restore_files = restore_files
        self.current_files = current_files
        self._head = "source_after_r11"
        self.available = {BAD, BASE, ROOT, self._head}
        self.root_tree = ROOT_TREE
        self.parents = {
            "source_after_r11": ("r11", "feature_after_r11"),
            "r11": ("r10",),
            "r10": ("r9",),
            "r9": ("r8",),
            "r8": ("source_after_root",),
            "source_after_root": (ROOT, "feature_after_root"),
            BAD: (BASE,),
        }
        self.trees = {
            ROOT: ROOT_TREE,
            "source_after_root": ROOT_TREE,
            "r8": "8" * 40,
            "r9": "9" * 40,
            "r10": "a" * 40,
            "r11": "b" * 40,
            "source_after_r11": "b" * 40,
            BAD: OLD_BAD_TREE,
            BASE: "c" * 40,
        }

    def _run_bytes(self, args):
        if args[:2] == ("cat-file", "-e"):
            sha = args[2].split("^", 1)[0]
            return SimpleNamespace(returncode=0 if sha in {BAD, BASE} else 1, stdout=b"", stderr=b"")
        if args and args[0] == "fetch":
            self.available.update({BAD, BASE})
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if args and args[0] == "ls-tree":
            sha = args[4]
            rows = sorted(self._files_for(sha))
            stdout = b"\x00".join(path.encode() for path in rows) + (b"\x00" if rows else b"")
            return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")
        if args and args[0] == "rev-list" and "--first-parent" in args:
            sha = args[-1]
            parents = self.parents.get(sha, ())
            line = " ".join((sha, *parents)).encode("ascii") + b"\n"
            return SimpleNamespace(returncode=0, stdout=line, stderr=b"")
        raise AssertionError(args)

    def _files_for(self, sha):
        if sha == ROOT:
            return self.root_files
        if sha == BASE:
            return self.restore_files
        if sha in {"r8", "r9", "r10", "r11", "source_after_r11", "source_after_root"}:
            return self.current_files if sha not in {ROOT, "source_after_root"} else self.root_files
        if sha == BAD:
            return self.restore_files
        return {}

    def root_commits(self):
        return (ROOT,)

    def head(self):
        return self._head

    def first_parent(self, sha):
        parents = self.parents.get(sha, ())
        return parents[0] if len(parents) == 1 else None

    def tree_oid(self, sha, path):
        assert path == "state"
        return self.trees[sha]

    def get_commit(self, sha):
        if sha == BAD:
            from shinobi_runtime.api.historical_repair_anchor import _ANCHOR
            return GitCommitRecord(
                BAD,
                ("state/meta.json", "state/martial-world/combats.json", "state/martial-world/people/house_tang.json"),
                {
                    TRANSACTION_TRAILER: _ANCHOR["damaged_transaction_id"],
                    CAMPAIGN_TRAILER: "jianghu-wei-main",
                    REVISION_TRAILER: str(_ANCHOR["damaged_revision"]),
                    MODE_TRAILER: "gameplay",
                    "Shinobi-Request": _ANCHOR["damaged_request_id"],
                    "Shinobi-Command-Digest": _ANCHOR["damaged_command_digest"],
                },
            )
        if sha in {"source_after_r11", "source_after_root"}:
            return GitCommitRecord(sha, ("runtime/source.py",), {})
        revision = int(sha[1:])
        return GitCommitRecord(
            sha,
            ("state/meta.json", "state/example.json"),
            {
                TRANSACTION_TRAILER: "tx.gameplay." + str(revision) * 64,
                CAMPAIGN_TRAILER: "jianghu-wei-main",
                REVISION_TRAILER: str(revision),
                MODE_TRAILER: "gameplay",
            },
        )

    def read_path_at(self, sha, path):
        if sha == ROOT:
            return self.root_files.get(path)
        if sha == BASE:
            return self.restore_files.get(path)
        if sha == BAD:
            if path == "state/meta.json":
                return meta(143, "SE-0061-09-27T22:58:33")
            if path == "state/martial-world/combats.json":
                return combat("active", 6212079)
            if path == "state/martial-world/people/house_tang.json":
                return people(3265)
            return self.restore_files.get(path)
        if sha in {"r8", "r9", "r10", "r11", "source_after_r11"}:
            return self.current_files.get(path)
        if sha == "source_after_root":
            return self.root_files.get(path)
        return None


class Coordinator:
    def __init__(self, git):
        self.git = git


def fixture():
    root_files = {
        "state/meta.json": meta(7, "SE-0061-09-27T21:16:52"),
        "state/martial-world/combats.json": combat("resolved", 109793, "side_a"),
        "state/martial-world/route-operations.json": route("contact_pending"),
        "state/martial-world/people/house_tang.json": people(0),
        "state/example.json": raw({"v": "packaged"}),
    }
    restore_files = {
        "state/meta.json": meta(142, "SE-0061-09-27T21:15:00"),
        "state/martial-world/combats.json": combat("active", 0),
        "state/martial-world/route-operations.json": route("contact_pending"),
        "state/martial-world/people/house_tang.json": people(0),
        "state/example.json": raw({"v": "prebattle"}),
    }
    current_files = dict(root_files)
    current_files["state/meta.json"] = meta(11, "SE-0061-09-27T21:22:13")
    current_files["state/example.json"] = raw({"v": "r11"})
    repo = Repo(current_files)
    git = Git(root_files, restore_files, current_files)
    return restore_files, repo, Coordinator(git)


def test_packaged_anchor_restores_exact_prebattle_parent_as_forward_revision():
    restore, repo, coordinator = fixture()
    build = build_packaged_battle_repair(
        anchor=PACKAGED_BATTLE_REPAIR_ANCHOR,
        campaign_id="jianghu-wei-main",
        expected_revision=11,
        repository=repo,
        coordinator=coordinator,
    )
    assert build.result["committed_revision"] == 12
    assert build.result["restored_state_revision"] == 142
    assert build.result["restored_world_time"] == "SE-0061-09-27T21:15:00"
    assert build.result["restored_combat_elapsed_ms"] == 0
    assert build.result["post_package_state_commit_count"] == 4
    repaired_meta = json.loads(build.writes["state/meta.json"])
    assert repaired_meta["revision"] == 12
    assert repaired_meta["time"] == "SE-0061-09-27T21:15:00"
    assert build.writes["state/martial-world/combats.json"] == restore["state/martial-world/combats.json"]


def test_packaged_anchor_fails_closed_on_wrong_root_or_missing_revision():
    _, repo, coordinator = fixture()
    coordinator.git.root_tree = "0" * 40
    coordinator.git.trees[ROOT] = coordinator.git.root_tree
    with pytest.raises(OperationError) as caught:
        build_packaged_battle_repair(
            anchor=PACKAGED_BATTLE_REPAIR_ANCHOR,
            campaign_id="jianghu-wei-main",
            expected_revision=11,
            repository=repo,
            coordinator=coordinator,
        )
    assert caught.value.code == "repair_packaged_root_snapshot_mismatch"

    _, repo, coordinator = fixture()
    coordinator.git.parents.pop("r10")
    with pytest.raises(OperationError) as caught:
        build_packaged_battle_repair(
            anchor=PACKAGED_BATTLE_REPAIR_ANCHOR,
            campaign_id="jianghu-wei-main",
            expected_revision=11,
            repository=repo,
            coordinator=coordinator,
        )
    assert caught.value.code in {"repair_packaged_history_invalid", "repair_packaged_history_incomplete"}


def test_source_merge_with_unchanged_state_is_allowed_but_state_changing_merge_is_not():
    _, repo, coordinator = fixture()
    build = build_packaged_battle_repair(
        anchor=PACKAGED_BATTLE_REPAIR_ANCHOR,
        campaign_id="jianghu-wei-main",
        expected_revision=11,
        repository=repo,
        coordinator=coordinator,
    )
    assert build.result["post_package_state_commit_count"] == 4

    _, repo, coordinator = fixture()
    coordinator.git.trees["source_after_r11"] = "f" * 40
    with pytest.raises(OperationError) as caught:
        build_packaged_battle_repair(
            anchor=PACKAGED_BATTLE_REPAIR_ANCHOR,
            campaign_id="jianghu-wei-main",
            expected_revision=11,
            repository=repo,
            coordinator=coordinator,
        )
    assert caught.value.code == "repair_packaged_history_invalid"


def test_production_entrypoint_does_not_install_one_time_battle_repair_anchor():
    text = (
        Path(__file__).resolve().parents[2]
        / "runtime/shinobi_runtime/api/campaign_entrypoint.py"
    ).read_text(encoding="utf-8")
    assert "install_packaged_battle_repair_anchor" not in text
    assert "install_historical_repair_anchor" not in text
