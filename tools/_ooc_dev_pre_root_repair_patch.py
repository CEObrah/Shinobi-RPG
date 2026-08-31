from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"expected exactly one match in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Add bounded Git object-fetch and tree-path helpers. These never move HEAD or refs.
git_path = ROOT / "runtime/shinobi_runtime/tx/git.py"
needle = '''    def is_ancestor(self, ancestor_commit: str, descendant_commit: str) -> bool:\n'''
insert = '''    def has_commit(self, commit_hash: str) -> bool:\n        \"\"\"Return whether one exact commit object is already available locally.\"\"\"\n        if (\n            not isinstance(commit_hash, str)\n            or len(commit_hash) != 40\n            or any(character not in \"0123456789abcdef\" for character in commit_hash)\n        ):\n            return False\n        completed = self._run_bytes((\"cat-file\", \"-e\", commit_hash + \"^{commit}\"))\n        return completed.returncode == 0\n\n    def fetch_ref_objects(self, remote: str, ref: str) -> None:\n        \"\"\"Fetch one bounded pull-request ref's objects without changing any local ref.\"\"\"\n        if (\n            not isinstance(remote, str)\n            or not remote\n            or len(remote) > 64\n            or any(character not in \"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-\" for character in remote)\n        ):\n            raise ValueError(\"invalid Git remote name\")\n        parts = ref.split(\"/\") if isinstance(ref, str) else []\n        if (\n            len(parts) != 4\n            or parts[:2] != [\"refs\", \"pull\"]\n            or not parts[2].isdigit()\n            or parts[3] != \"head\"\n        ):\n            raise ValueError(\"historical object fetch requires an exact pull-request head ref\")\n        before_head = self.head()\n        completed = self._run_bytes((\n            \"fetch\", \"--no-tags\", \"--no-write-fetch-head\", remote, ref,\n        ))\n        if completed.returncode:\n            raise GitStageError(\n                completed.returncode,\n                completed.stderr.decode(\"utf-8\", errors=\"replace\"),\n            )\n        if self.head() != before_head:\n            raise GitStageError(1, \"bounded historical fetch changed HEAD\")\n\n    def list_paths_at(self, commit_hash: str, relative_path: str) -> Tuple[str, ...]:\n        \"\"\"List exact tracked file paths below one tree without checking it out.\"\"\"\n        normalized = normalize_relative_path(relative_path)\n        return self._path_output((\n            \"ls-tree\", \"-r\", \"--name-only\", \"-z\", commit_hash, \"--\", normalized,\n        ))\n\n    def is_ancestor(self, ancestor_commit: str, descendant_commit: str) -> bool:\n'''
replace_once(git_path, needle, insert)


historical_path = ROOT / "runtime/shinobi_runtime/api/historical_repair.py"
historical_path.write_text(r'''"""Closed historical repair anchor for the rev143 exact-combat incident.

This module is intentionally campaign-specific.  It does not accept repository
refs, commits, paths, values, or URLs from a caller.  The only supported anchor
is a server-owned identity whose old world lineage remains immutable on GitHub
through PR #121.  Current state continuity from the rewritten rev143 release
root through the live revision is independently proven from committed WALs.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.tx.errors import GitStageError, WalError
from shinobi_runtime.tx.git import (
    CAMPAIGN_TRAILER,
    COMMAND_DIGEST_TRAILER,
    MODE_TRAILER,
    REQUEST_TRAILER,
    REVISION_TRAILER,
    TRANSACTION_TRAILER,
)

HISTORICAL_COMBAT_ANCHOR = "jianghu.pre_root.rev143_combat_grind.v1"
_META_PATH = "state/meta.json"
_STATE_PREFIX = "state"

# This identity is fixed in source so the repair transport cannot be repurposed
# as a generic historical checkout or arbitrary-state write primitive.
_ANCHOR = {
    "campaign_id": "jianghu-wei-main",
    "remote": "origin",
    "fetch_ref": "refs/pull/121/head",
    "release_root": "47fcd196a87c301daf7566d92edf317604ca15bc",
    "damaged_commit": "df686d903b2ed526030ebffcd7997040968725ae",
    "damaged_transaction_id": "tx.gameplay.40dd7acfe68c326566ff6130e271e949897253cf662eb713005dcfcd768d0be4",
    "damaged_request_id": "play.combat.resume.attack.r142",
    "damaged_command_digest": "40dd7acfe68c326566ff6130e271e949897253cf662eb713005dcfcd768d0be4",
    "damaged_revision": 143,
    "damaged_time": "SE-0061-09-27T22:58:33",
    "damaged_combat_elapsed_ms": 6212079,
    "damaged_player_fatigue_milli": 3265,
    "restore_commit": "63556b9cbcfcd96bbb0f938fc5ce31f41a3fa92a",
    "restore_revision": 142,
    "restore_time": "SE-0061-09-27T21:15:00",
    "restore_combat_elapsed_ms": 0,
    "restore_player_fatigue_milli": 0,
    "combat_ref": "combat:contact:escort_muster:0e52cfa45f5bbea72ba0:0061-09-27:black_lance_company",
    "player_id": "pc_wei_tang",
}


@dataclass(frozen=True)
class HistoricalRepairBuild:
    writes: Mapping[str, Optional[bytes]]
    result: Mapping[str, Any]
    affected_refs: tuple[str, ...]


def _sha(value: Optional[bytes]) -> Optional[str]:
    return None if value is None else hashlib.sha256(value).hexdigest()


def _decode_image(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OperationError(409, "repair_historical_provenance_invalid")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise OperationError(409, "repair_historical_provenance_invalid") from exc


def _json_at(git: Any, commit: str, path: str) -> Mapping[str, Any]:
    raw = git.read_path_at(commit, path)
    if raw is None:
        raise OperationError(409, "repair_historical_provenance_invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationError(409, "repair_historical_provenance_invalid") from exc
    if not isinstance(value, Mapping):
        raise OperationError(409, "repair_historical_provenance_invalid")
    return value


def _combat_elapsed(git: Any, commit: str) -> int:
    state = _json_at(git, commit, "state/martial-world/combats.json")
    combats = state.get("combats")
    row = combats.get(_ANCHOR["combat_ref"]) if isinstance(combats, Mapping) else None
    if not isinstance(row, Mapping) or row.get("status") != "active":
        raise OperationError(409, "repair_historical_provenance_invalid")
    value = row.get("elapsed_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OperationError(409, "repair_historical_provenance_invalid")
    return value


def _player_fatigue(git: Any, commit: str) -> int:
    state = _json_at(git, commit, "state/martial-world/people/house_tang.json")
    rows = state.get("people")
    if not isinstance(rows, list):
        raise OperationError(409, "repair_historical_provenance_invalid")
    for row in rows:
        if isinstance(row, Mapping) and row.get("person_id") == _ANCHOR["player_id"]:
            value = row.get("fatigue_milli", 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise OperationError(409, "repair_historical_provenance_invalid")
            return value
    raise OperationError(409, "repair_historical_provenance_invalid")


def _ensure_anchor_objects(git: Any) -> None:
    damaged = str(_ANCHOR["damaged_commit"])
    restore = str(_ANCHOR["restore_commit"])
    if git.has_commit(damaged) and git.has_commit(restore):
        return
    try:
        git.fetch_ref_objects(str(_ANCHOR["remote"]), str(_ANCHOR["fetch_ref"]))
    except (GitStageError, OSError, ValueError) as exc:
        raise OperationError(503, "repair_historical_anchor_unavailable") from exc
    if not git.has_commit(damaged) or not git.has_commit(restore):
        raise OperationError(409, "repair_historical_anchor_unavailable")


def _verify_anchor(git: Any, campaign_id: str) -> None:
    if campaign_id != _ANCHOR["campaign_id"]:
        raise OperationError(409, "repair_historical_campaign_mismatch")
    _ensure_anchor_objects(git)

    roots = git.root_commits()
    if roots != (str(_ANCHOR["release_root"]),):
        raise OperationError(409, "repair_historical_release_root_mismatch")

    damaged = git.get_commit(str(_ANCHOR["damaged_commit"]))
    if (
        damaged.trailers.get(TRANSACTION_TRAILER) != _ANCHOR["damaged_transaction_id"]
        or damaged.trailers.get(CAMPAIGN_TRAILER) != campaign_id
        or damaged.trailers.get(REVISION_TRAILER) != str(_ANCHOR["damaged_revision"])
        or damaged.trailers.get(MODE_TRAILER) != "gameplay"
        or damaged.trailers.get(REQUEST_TRAILER) != _ANCHOR["damaged_request_id"]
        or damaged.trailers.get(COMMAND_DIGEST_TRAILER) != _ANCHOR["damaged_command_digest"]
        or _META_PATH not in damaged.paths
        or any(not path.startswith("state/") for path in damaged.paths)
    ):
        raise OperationError(409, "repair_historical_provenance_invalid")
    if git.first_parent(str(_ANCHOR["damaged_commit"])) != _ANCHOR["restore_commit"]:
        raise OperationError(409, "repair_historical_provenance_invalid")
    if git.tree_oid(str(_ANCHOR["damaged_commit"]), _STATE_PREFIX) != git.tree_oid(str(_ANCHOR["release_root"]), _STATE_PREFIX):
        raise OperationError(409, "repair_historical_release_snapshot_mismatch")

    damaged_meta = _json_at(git, str(_ANCHOR["damaged_commit"]), _META_PATH)
    restore_meta = _json_at(git, str(_ANCHOR["restore_commit"]), _META_PATH)
    if (
        damaged_meta.get("campaign_id") != campaign_id
        or damaged_meta.get("revision") != _ANCHOR["damaged_revision"]
        or damaged_meta.get("time") != _ANCHOR["damaged_time"]
        or restore_meta.get("campaign_id") != campaign_id
        or restore_meta.get("revision") != _ANCHOR["restore_revision"]
        or restore_meta.get("time") != _ANCHOR["restore_time"]
        or _combat_elapsed(git, str(_ANCHOR["damaged_commit"])) != _ANCHOR["damaged_combat_elapsed_ms"]
        or _combat_elapsed(git, str(_ANCHOR["restore_commit"])) != _ANCHOR["restore_combat_elapsed_ms"]
        or _player_fatigue(git, str(_ANCHOR["damaged_commit"])) != _ANCHOR["damaged_player_fatigue_milli"]
        or _player_fatigue(git, str(_ANCHOR["restore_commit"])) != _ANCHOR["restore_player_fatigue_milli"]
    ):
        raise OperationError(409, "repair_historical_provenance_invalid")


def _wal_records(coordinator: Any, campaign_id: str, start_revision: int, end_revision: int) -> list[Mapping[str, Any]]:
    try:
        records = coordinator.wal.records(("committed",))
    except (OSError, WalError) as exc:
        raise OperationError(409, "repair_historical_wal_invalid") from exc
    by_revision: dict[int, Mapping[str, Any]] = {}
    for record in records:
        manifest = record.get("manifest", {}) if isinstance(record, Mapping) else {}
        if not isinstance(manifest, Mapping) or manifest.get("campaign_id") != campaign_id:
            continue
        target = manifest.get("target_revision")
        if isinstance(target, bool) or not isinstance(target, int) or not start_revision <= target <= end_revision:
            continue
        if target in by_revision:
            raise OperationError(409, "repair_historical_wal_invalid")
        by_revision[target] = record
    expected = list(range(start_revision, end_revision + 1))
    if sorted(by_revision) != expected:
        raise OperationError(409, "repair_historical_wal_incomplete")
    return [by_revision[revision] for revision in expected]


def _verify_current_from_release_root(*, repository: Any, coordinator: Any, expected_revision: int) -> list[str]:
    git = coordinator.git
    release_root = str(_ANCHOR["release_root"])
    start_revision = int(_ANCHOR["damaged_revision"]) + 1
    if expected_revision < start_revision:
        raise OperationError(409, "repair_historical_revision_invalid")
    records = _wal_records(coordinator, str(_ANCHOR["campaign_id"]), start_revision, expected_revision)
    root_paths = set(git.list_paths_at(release_root, _STATE_PREFIX))
    if _META_PATH not in root_paths:
        raise OperationError(409, "repair_historical_provenance_invalid")

    last_after: dict[str, Optional[str]] = {}
    transaction_ids: list[str] = []
    for revision, record in zip(range(start_revision, expected_revision + 1), records):
        manifest = record.get("manifest", {})
        transaction_id = record.get("transaction_id")
        if (
            manifest.get("base_revision") != revision - 1
            or manifest.get("target_revision") != revision
            or manifest.get("mode") not in {"gameplay", "autonomous", "repair"}
            or not isinstance(transaction_id, str)
            or manifest.get("transaction_id") != transaction_id
        ):
            raise OperationError(409, "repair_historical_wal_invalid")
        entries = record.get("entries")
        if not isinstance(entries, list) or not entries:
            raise OperationError(409, "repair_historical_wal_invalid")
        seen: set[str] = set()
        has_meta = False
        for entry in entries:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
                raise OperationError(409, "repair_historical_wal_invalid")
            path = str(entry["path"])
            if not path.startswith("state/") or path in seen:
                raise OperationError(409, "repair_historical_wal_invalid")
            seen.add(path)
            has_meta = has_meta or path == _META_PATH
            expected_before = last_after.get(path, _sha(git.read_path_at(release_root, path)))
            if entry.get("before_sha256") != expected_before:
                raise OperationError(409, "repair_historical_wal_discontinuous")
            after = entry.get("after_sha256")
            if after is not None and not isinstance(after, str):
                raise OperationError(409, "repair_historical_wal_invalid")
            # WAL.records already validates the image digests; decode the meta
            # after-image here to additionally prove world-revision continuity.
            if path == _META_PATH:
                meta_raw = _decode_image(entry.get("after_b64"))
                if meta_raw is None:
                    raise OperationError(409, "repair_historical_wal_invalid")
                try:
                    meta = json.loads(meta_raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise OperationError(409, "repair_historical_wal_invalid") from exc
                if not isinstance(meta, Mapping) or meta.get("campaign_id") != _ANCHOR["campaign_id"] or meta.get("revision") != revision:
                    raise OperationError(409, "repair_historical_wal_invalid")
            last_after[path] = after
        if not has_meta:
            raise OperationError(409, "repair_historical_wal_invalid")
        transaction_ids.append(transaction_id)

    expected_paths = set(root_paths)
    for path, digest in last_after.items():
        if digest is None:
            expected_paths.discard(path)
        else:
            expected_paths.add(path)
    head_paths = set(git.list_paths_at(git.head(), _STATE_PREFIX))
    if head_paths != expected_paths:
        raise OperationError(409, "repair_historical_current_state_mismatch")
    for path in sorted(expected_paths):
        expected_digest = last_after.get(path, _sha(git.read_path_at(release_root, path)))
        if repository.digest(path) != expected_digest:
            raise OperationError(409, "repair_historical_current_state_mismatch")
    return transaction_ids


def build_historical_anchor_repair(*, anchor: str, campaign_id: str, expected_revision: int, repository: Any, coordinator: Any) -> HistoricalRepairBuild:
    if anchor != HISTORICAL_COMBAT_ANCHOR:
        raise OperationError(422, "repair_payload_invalid")
    if campaign_id != _ANCHOR["campaign_id"]:
        raise OperationError(409, "repair_historical_campaign_mismatch")
    if expected_revision < int(_ANCHOR["damaged_revision"]) + 1:
        raise OperationError(409, "repair_historical_revision_invalid")

    git = coordinator.git
    _verify_anchor(git, campaign_id)
    transaction_ids = _verify_current_from_release_root(
        repository=repository, coordinator=coordinator, expected_revision=expected_revision,
    )

    restore_commit = str(_ANCHOR["restore_commit"])
    restore_meta = dict(_json_at(git, restore_commit, _META_PATH))
    restore_paths = set(git.list_paths_at(restore_commit, _STATE_PREFIX))
    current_paths = set(git.list_paths_at(git.head(), _STATE_PREFIX))
    if _META_PATH not in restore_paths:
        raise OperationError(409, "repair_historical_provenance_invalid")

    writes: dict[str, Optional[bytes]] = {}
    for path in sorted(restore_paths | current_paths):
        desired = git.read_path_at(restore_commit, path) if path in restore_paths else None
        if path == _META_PATH:
            restore_meta["revision"] = expected_revision + 1
            desired = (json.dumps(restore_meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if repository.read_optional_bytes(path) != desired:
            writes[path] = desired
    if _META_PATH not in writes:
        raise OperationError(409, "repair_historical_provenance_invalid")

    result = {
        "repair_kind": "forward_trusted_historical_anchor_repair",
        "historical_anchor": HISTORICAL_COMBAT_ANCHOR,
        "restored_state_revision": _ANCHOR["restore_revision"],
        "committed_revision": expected_revision + 1,
        "restored_world_time": _ANCHOR["restore_time"],
        "restored_combat_elapsed_ms": _ANCHOR["restore_combat_elapsed_ms"],
        "historical_damaged_revision": _ANCHOR["damaged_revision"],
        "historical_damaged_transaction_id": _ANCHOR["damaged_transaction_id"],
        "historical_damaged_commit": _ANCHOR["damaged_commit"],
        "restore_commit": _ANCHOR["restore_commit"],
        "release_root": _ANCHOR["release_root"],
        "wal_revision_start": int(_ANCHOR["damaged_revision"]) + 1,
        "wal_revision_end": expected_revision,
        "wal_transaction_count": len(transaction_ids),
        "repaired_path_count": len(writes),
        "provenance_source": "trusted_pre_root_git_anchor_plus_committed_wal",
    }
    return HistoricalRepairBuild(writes=writes, result=result, affected_refs=tuple(sorted(writes)))


__all__ = ["HISTORICAL_COMBAT_ANCHOR", "HistoricalRepairBuild", "build_historical_anchor_repair"]
''', encoding="utf-8")


repair_path = ROOT / "runtime/shinobi_runtime/api/repair.py"
replace_once(
    repair_path,
    'from shinobi_runtime.api.operations import CampaignOperations, OperationError, PlanStateChangedError\n',
    'from shinobi_runtime.api.operations import CampaignOperations, OperationError, PlanStateChangedError\nfrom shinobi_runtime.api.historical_repair import HISTORICAL_COMBAT_ANCHOR, build_historical_anchor_repair\n',
)
replace_once(
    repair_path,
    '''class _RepairRequest:\n    transaction_ids: tuple[str, ...] = ()\n    wal_revision_start: Optional[int] = None\n''',
    '''class _RepairRequest:\n    transaction_ids: tuple[str, ...] = ()\n    wal_revision_start: Optional[int] = None\n    historical_anchor: Optional[str] = None\n''',
)
replace_once(
    repair_path,
    '''        if keys == {"damaged_wal_revision_start"}:\n            raw_start = command.payload.get("damaged_wal_revision_start")\n            if (\n                isinstance(raw_start, bool)\n                or not isinstance(raw_start, int)\n                or raw_start < 1\n                or raw_start > command.expected_revision\n                or command.expected_revision - raw_start + 1 > _MAX_WAL_REPAIR_CHAIN\n            ):\n                raise OperationError(422, "repair_payload_invalid")\n            request = _RepairRequest(wal_revision_start=raw_start)\n        else:\n''',
    '''        if keys == {"historical_anchor"}:\n            raw_anchor = command.payload.get("historical_anchor")\n            if raw_anchor != HISTORICAL_COMBAT_ANCHOR:\n                raise OperationError(422, "repair_payload_invalid")\n            request = _RepairRequest(historical_anchor=str(raw_anchor))\n        elif keys == {"damaged_wal_revision_start"}:\n            raw_start = command.payload.get("damaged_wal_revision_start")\n            if (\n                isinstance(raw_start, bool)\n                or not isinstance(raw_start, int)\n                or raw_start < 1\n                or raw_start > command.expected_revision\n                or command.expected_revision - raw_start + 1 > _MAX_WAL_REPAIR_CHAIN\n            ):\n                raise OperationError(422, "repair_payload_invalid")\n            request = _RepairRequest(wal_revision_start=raw_start)\n        else:\n''',
)
replace_once(
    repair_path,
    '''        self._require_fresh_deployment()\n        if repair_request.wal_revision_start is not None:\n            return self._build_wal_chain(command, repair_request.wal_revision_start)\n''',
    '''        self._require_fresh_deployment()\n        if repair_request.historical_anchor is not None:\n            historical = build_historical_anchor_repair(\n                anchor=repair_request.historical_anchor,\n                campaign_id=command.campaign_id,\n                expected_revision=command.expected_revision,\n                repository=self.repository,\n                coordinator=self.coordinator,\n            )\n            return _RepairPlan(\n                transaction_id="tx.repair." + command.digest,\n                created_at=command.submitted_at,\n                writes=historical.writes,\n                result=historical.result,\n                affected_refs=historical.affected_refs,\n            )\n        if repair_request.wal_revision_start is not None:\n            return self._build_wal_chain(command, repair_request.wal_revision_start)\n''',
)


test_path = ROOT / "tests/current/test_historical_anchor_repair.py"
test_path.write_text(r'''import base64
import hashlib
import json
from contextlib import nullcontext

import pytest

from shinobi_runtime.api.historical_repair import HISTORICAL_COMBAT_ANCHOR, build_historical_anchor_repair
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.repair import CampaignRepairService, REPAIR_COMMAND_TYPE
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.tx.git import (
    CAMPAIGN_TRAILER, COMMAND_DIGEST_TRAILER, MODE_TRAILER, REQUEST_TRAILER,
    REVISION_TRAILER, TRANSACTION_TRAILER, GitCommitRecord,
)

BAD = "df686d903b2ed526030ebffcd7997040968725ae"
BASE = "63556b9cbcfcd96bbb0f938fc5ce31f41a3fa92a"
ROOT = "47fcd196a87c301daf7566d92edf317604ca15bc"
TX = "tx.gameplay.40dd7acfe68c326566ff6130e271e949897253cf662eb713005dcfcd768d0be4"


def raw(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def digest(value):
    return None if value is None else hashlib.sha256(value).hexdigest()


def image(value):
    return None if value is None else base64.b64encode(value).decode("ascii")


def meta(rev, time):
    return raw({"schema":"meta","campaign_id":"jianghu-wei-main","game":"jianghu","revision":rev,"time":time,"player_id":"pc_wei_tang"})


def combats(elapsed):
    return raw({"schema":"jianghu-combat-state-1.0","combats":{"combat:contact:escort_muster:0e52cfa45f5bbea72ba0:0061-09-27:black_lance_company":{"status":"active","elapsed_ms":elapsed}}})


def people(fatigue):
    row = {"person_id":"pc_wei_tang"}
    if fatigue:
        row["fatigue_milli"] = fatigue
    return raw({"schema":"jianghu-person-lite-roster-1.0","faction_ref":"house_tang","people":[row]})


class Repo:
    def __init__(self, files): self.files = dict(files)
    def read_optional_bytes(self, path): return self.files.get(path)
    def digest(self, path): return digest(self.files.get(path))
    def require_campaign(self, expected, path="state/meta.json"):
        assert json.loads(self.files[path])["campaign_id"] == expected
    def require_revision(self, expected, path="state/meta.json"):
        if json.loads(self.files[path])["revision"] != expected:
            from shinobi_runtime.tx.errors import StaleRevisionError
            raise StaleRevisionError(expected, json.loads(self.files[path])["revision"])


class Git:
    def __init__(self, trees, files, head="head"):
        self.trees=trees; self.files=files; self._head=head; self.fetched=[]
    def has_commit(self, sha): return sha in {BAD, BASE, ROOT}
    def fetch_ref_objects(self, remote, ref): self.fetched.append((remote,ref))
    def root_commits(self): return (ROOT,)
    def head(self): return self._head
    def get_commit(self, sha):
        assert sha == BAD
        return GitCommitRecord(BAD,("state/meta.json","state/martial-world/combats.json","state/martial-world/people/house_tang.json"),{
            TRANSACTION_TRAILER:TX,CAMPAIGN_TRAILER:"jianghu-wei-main",REVISION_TRAILER:"143",MODE_TRAILER:"gameplay",
            REQUEST_TRAILER:"play.combat.resume.attack.r142",COMMAND_DIGEST_TRAILER:"40dd7acfe68c326566ff6130e271e949897253cf662eb713005dcfcd768d0be4",
        })
    def first_parent(self, sha): assert sha==BAD; return BASE
    def tree_oid(self, sha, path): assert path=="state"; return self.trees[sha]
    def read_path_at(self, sha, path): return self.files.get((sha,path))
    def list_paths_at(self, sha, prefix):
        assert prefix=="state"
        return tuple(sorted(path for (commit,path) in self.files if commit==sha and path.startswith("state/")))


class Wal:
    def __init__(self, records): self._records=records
    def records(self, statuses): assert tuple(statuses)==("committed",); return tuple(self._records)


class Coordinator:
    def __init__(self, git, wal): self.git=git; self.wal=wal


def wal_record(rev, before_files, after_files, tx):
    entries=[]
    paths=sorted(set(before_files)|set(after_files))
    for path in paths:
        before=before_files.get(path); after=after_files.get(path)
        entries.append({"path":path,"before_sha256":digest(before),"after_sha256":digest(after),"before_b64":image(before),"after_b64":image(after)})
    return {"transaction_id":tx,"manifest":{"transaction_id":tx,"campaign_id":"jianghu-wei-main","base_revision":rev-1,"target_revision":rev,"mode":"gameplay"},"entries":entries}


def fixture():
    base={"state/meta.json":meta(142,"SE-0061-09-27T21:15:00"),"state/martial-world/combats.json":combats(0),"state/martial-world/people/house_tang.json":people(0),"state/example.json":raw({"v":"base"})}
    root={"state/meta.json":meta(143,"SE-0061-09-27T22:58:33"),"state/martial-world/combats.json":combats(6212079),"state/martial-world/people/house_tang.json":people(3265),"state/example.json":raw({"v":"bad"})}
    r144=dict(root); r144["state/meta.json"]=meta(144,"SE-0061-09-27T22:59:22"); r144["state/example.json"]=raw({"v":"144"})
    r145=dict(r144); r145["state/meta.json"]=meta(145,"SE-0061-09-28T00:53:58"); r145["state/example.json"]=raw({"v":"145"})
    files={}
    for sha, rows in ((BASE,base),(BAD,root),(ROOT,root),("head",r145)):
        for path,value in rows.items(): files[(sha,path)]=value
    git=Git({BASE:"base-tree",BAD:"root-state",ROOT:"root-state","head":"head-state"},files)
    wal=Wal([wal_record(144,root,r144,"tx.gameplay."+"1"*64),wal_record(145,r144,r145,"tx.gameplay."+"2"*64)])
    return base,root,r145,Repo(r145),Coordinator(git,wal)


def test_trusted_historical_anchor_restores_exact_rev142_state_forward():
    base,_,_,repo,coordinator=fixture()
    build=build_historical_anchor_repair(anchor=HISTORICAL_COMBAT_ANCHOR,campaign_id="jianghu-wei-main",expected_revision=145,repository=repo,coordinator=coordinator)
    assert build.result["restored_state_revision"] == 142
    assert build.result["committed_revision"] == 146
    assert build.result["restored_world_time"] == "SE-0061-09-27T21:15:00"
    assert build.result["restored_combat_elapsed_ms"] == 0
    assert build.result["provenance_source"] == "trusted_pre_root_git_anchor_plus_committed_wal"
    repaired_meta=json.loads(build.writes["state/meta.json"])
    assert repaired_meta["revision"] == 146
    assert repaired_meta["time"] == "SE-0061-09-27T21:15:00"
    assert build.writes["state/martial-world/combats.json"] == base["state/martial-world/combats.json"]
    assert build.writes["state/martial-world/people/house_tang.json"] == base["state/martial-world/people/house_tang.json"]


def test_historical_anchor_requires_complete_wal_chain_and_exact_current_images():
    _,_,current,repo,coordinator=fixture()
    coordinator.wal._records = coordinator.wal._records[1:]
    with pytest.raises(OperationError) as caught:
        build_historical_anchor_repair(anchor=HISTORICAL_COMBAT_ANCHOR,campaign_id="jianghu-wei-main",expected_revision=145,repository=repo,coordinator=coordinator)
    assert caught.value.code == "repair_historical_wal_incomplete"

    _,_,current,repo,coordinator=fixture()
    repo.files["state/example.json"] = raw({"v":"tampered"})
    with pytest.raises(OperationError) as caught:
        build_historical_anchor_repair(anchor=HISTORICAL_COMBAT_ANCHOR,campaign_id="jianghu-wei-main",expected_revision=145,repository=repo,coordinator=coordinator)
    assert caught.value.code == "repair_historical_current_state_mismatch"


def test_historical_anchor_fails_if_release_snapshot_does_not_equal_bad_rev143_state():
    _,_,_,repo,coordinator=fixture()
    coordinator.git.trees[ROOT] = "different"
    with pytest.raises(OperationError) as caught:
        build_historical_anchor_repair(anchor=HISTORICAL_COMBAT_ANCHOR,campaign_id="jianghu-wei-main",expected_revision=145,repository=repo,coordinator=coordinator)
    assert caught.value.code == "repair_historical_release_snapshot_mismatch"


def test_repair_payload_exposes_only_closed_historical_anchor():
    _,_,_,repo,coordinator=fixture()
    class Ops:
        repository=repo; coordinator=coordinator; allowed_actor_ids=frozenset({"pc_wei_tang"})
    service=CampaignRepairService(Ops())
    def command(value):
        return CommandEnvelope(campaign_id="jianghu-wei-main",request_id="repair.test",actor_id="pc_wei_tang",command_type=REPAIR_COMMAND_TYPE,expected_revision=145,submitted_at="2026-08-31T00:00:00Z",mode="repair",payload={"historical_anchor":value})
    request=service._require_base(command(HISTORICAL_COMBAT_ANCHOR))
    assert request.historical_anchor == HISTORICAL_COMBAT_ANCHOR
    with pytest.raises(OperationError) as caught:
        service._require_base(command("arbitrary.sha.or.path"))
    assert caught.value.code == "repair_payload_invalid"
''', encoding="utf-8")

print("patched pre-root historical repair")
