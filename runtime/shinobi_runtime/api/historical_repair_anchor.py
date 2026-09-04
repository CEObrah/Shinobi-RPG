"""Campaign-specific historical repair anchor for the rev143 combat incident.

The reachable repository was intentionally rewritten to a rev143 release root,
but GitHub still retains the original rev143 gameplay transaction through the
old PR #121 lineage.  This module adds exactly one closed repair identity for
that incident.  The caller cannot choose a Git ref, commit, path, value, URL, or
revision baseline.  The fixed historical lineage is verified cryptographically,
then the current rev143->live state evolution is proved from committed local
WAL records before any forward repair plan is produced.

This helper is retained for forensic/replay validation of the original incident
and explicit disposable-copy investigations. It is deliberately not installed by
the canonical production entrypoint after the repaired state became part of the
packaged baseline and production recovery stores became fresh. Generic repair
behavior therefore remains unchanged in normal live composition.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.tx.errors import GitStageError, StaleRevisionError, WalError
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
_EXPECTED_STATE_TREE = "a8ec71acec5d0ca8c129f0bae70823d0a7445659"
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


@dataclass(frozen=True)
class _HistoricalRequest:
    historical_anchor: str


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


def _git_has_commit(git: Any, commit: str) -> bool:
    completed = git._run_bytes(("cat-file", "-e", commit + "^{commit}"))
    return completed.returncode == 0


def _git_list_paths(git: Any, commit: str) -> tuple[str, ...]:
    completed = git._run_bytes((
        "ls-tree", "-r", "--name-only", "-z", commit, "--", _STATE_PREFIX,
    ))
    if completed.returncode:
        raise GitStageError(
            completed.returncode,
            completed.stderr.decode("utf-8", errors="replace"),
        )
    return tuple(sorted(
        item.decode("utf-8")
        for item in completed.stdout.split(b"\x00")
        if item
    ))


def _ensure_anchor_objects(git: Any) -> None:
    damaged = str(_ANCHOR["damaged_commit"])
    restore = str(_ANCHOR["restore_commit"])
    if _git_has_commit(git, damaged) and _git_has_commit(git, restore):
        return
    before_head = git.head()
    completed = git._run_bytes((
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        str(_ANCHOR["remote"]),
        str(_ANCHOR["fetch_ref"]),
    ))
    if completed.returncode:
        raise OperationError(503, "repair_historical_anchor_unavailable")
    if git.head() != before_head:
        raise OperationError(409, "repair_historical_anchor_changed_head")
    if not _git_has_commit(git, damaged) or not _git_has_commit(git, restore):
        raise OperationError(409, "repair_historical_anchor_unavailable")


def _combat_elapsed(git: Any, commit: str) -> int:
    state = _json_at(git, commit, "state/martial-world/combats.json")
    combats = state.get("combats")
    row = combats.get(_ANCHOR["combat_ref"]) if isinstance(combats, Mapping) else None
    value = row.get("elapsed_ms") if isinstance(row, Mapping) and row.get("status") == "active" else None
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

    damaged_tree = git.tree_oid(str(_ANCHOR["damaged_commit"]), _STATE_PREFIX)
    release_tree = git.tree_oid(str(_ANCHOR["release_root"]), _STATE_PREFIX)
    if damaged_tree != _EXPECTED_STATE_TREE or release_tree != _EXPECTED_STATE_TREE:
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
    root_paths = set(_git_list_paths(git, release_root))
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
            if path == _META_PATH:
                meta_raw = _decode_image(entry.get("after_b64"))
                if meta_raw is None:
                    raise OperationError(409, "repair_historical_wal_invalid")
                try:
                    meta = json.loads(meta_raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise OperationError(409, "repair_historical_wal_invalid") from exc
                if (
                    not isinstance(meta, Mapping)
                    or meta.get("campaign_id") != _ANCHOR["campaign_id"]
                    or meta.get("revision") != revision
                ):
                    raise OperationError(409, "repair_historical_wal_invalid")
            last_after[path] = after
        if not has_meta:
            raise OperationError(409, "repair_historical_wal_invalid")
        transaction_ids.append(transaction_id)

    expected_paths = set(root_paths)
    for path, digest_value in last_after.items():
        if digest_value is None:
            expected_paths.discard(path)
        else:
            expected_paths.add(path)
    head_paths = set(_git_list_paths(git, git.head()))
    if head_paths != expected_paths:
        raise OperationError(409, "repair_historical_current_state_mismatch")
    for path in sorted(expected_paths):
        expected_digest = last_after.get(path, _sha(git.read_path_at(release_root, path)))
        if repository.digest(path) != expected_digest:
            raise OperationError(409, "repair_historical_current_state_mismatch")
    return transaction_ids


def build_historical_anchor_repair(
    *, anchor: str, campaign_id: str, expected_revision: int, repository: Any, coordinator: Any,
) -> HistoricalRepairBuild:
    if anchor != HISTORICAL_COMBAT_ANCHOR:
        raise OperationError(422, "repair_payload_invalid")
    if campaign_id != _ANCHOR["campaign_id"]:
        raise OperationError(409, "repair_historical_campaign_mismatch")
    if expected_revision < int(_ANCHOR["damaged_revision"]) + 1:
        raise OperationError(409, "repair_historical_revision_invalid")

    git = coordinator.git
    _verify_anchor(git, campaign_id)
    transaction_ids = _verify_current_from_release_root(
        repository=repository,
        coordinator=coordinator,
        expected_revision=expected_revision,
    )

    restore_commit = str(_ANCHOR["restore_commit"])
    restore_meta = dict(_json_at(git, restore_commit, _META_PATH))
    restore_paths = set(_git_list_paths(git, restore_commit))
    current_paths = set(_git_list_paths(git, git.head()))
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
    return HistoricalRepairBuild(
        writes=writes,
        result=result,
        affected_refs=tuple(sorted(writes)),
    )


def _validate_historical_command(self: Any, command: CommandEnvelope, *, require_revision: bool) -> _HistoricalRequest:
    from shinobi_runtime.api.repair import REPAIR_COMMAND_TYPE, REPAIR_MODE

    if command.mode != REPAIR_MODE or command.command_type != REPAIR_COMMAND_TYPE:
        raise OperationError(403, "repair_mode_required")
    if command.actor_id not in self.operations.allowed_actor_ids:
        raise OperationError(403, "actor_not_allowed")
    if set(command.payload) != {"historical_anchor"} or command.payload.get("historical_anchor") != HISTORICAL_COMBAT_ANCHOR:
        raise OperationError(422, "repair_payload_invalid")
    try:
        self.repository.require_campaign(command.campaign_id, _META_PATH)
        if require_revision:
            self.repository.require_revision(command.expected_revision, _META_PATH)
    except StaleRevisionError as exc:
        raise OperationError(409, "stale_revision") from exc
    except (TypeError, ValueError) as exc:
        raise OperationError(409, "repair_campaign_mismatch") from exc
    return _HistoricalRequest(HISTORICAL_COMBAT_ANCHOR)


def install_historical_repair_anchor() -> None:
    """Compose the one closed historical repair identity into production repair."""
    from shinobi_runtime.api import repair as repair_module

    service_type = repair_module.CampaignRepairService
    if getattr(service_type, "_historical_anchor_installed", False):
        return
    original_require_base = service_type._require_base
    original_build = service_type._build

    def require_base(self: Any, command: CommandEnvelope, *, require_revision: bool = True):
        if set(command.payload) == {"historical_anchor"}:
            return _validate_historical_command(self, command, require_revision=require_revision)
        return original_require_base(self, command, require_revision=require_revision)

    def build(self: Any, command: CommandEnvelope):
        if set(command.payload) != {"historical_anchor"}:
            return original_build(self, command)
        request = self._require_base(command)
        self._require_fresh_deployment()
        historical = build_historical_anchor_repair(
            anchor=request.historical_anchor,
            campaign_id=command.campaign_id,
            expected_revision=command.expected_revision,
            repository=self.repository,
            coordinator=self.coordinator,
        )
        return repair_module._RepairPlan(
            transaction_id="tx.repair." + command.digest,
            created_at=command.submitted_at,
            writes=historical.writes,
            result=historical.result,
            affected_refs=historical.affected_refs,
        )

    service_type._require_base = require_base
    service_type._build = build
    service_type._historical_anchor_installed = True


__all__ = [
    "HISTORICAL_COMBAT_ANCHOR",
    "HistoricalRepairBuild",
    "build_historical_anchor_repair",
    "install_historical_repair_anchor",
]
