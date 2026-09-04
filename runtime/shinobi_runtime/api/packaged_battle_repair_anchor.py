"""Closed forward repair for the packaged Black Lance road-battle replay.

The September 4 packaged campaign root already contained the historical player
road combat in a terminal state. That made the battle impossible to present or
play even though the original incident still has immutable Git provenance for
an exact pre-fight parent. This module bridges only those two fixed identities:

* the exact packaged root/state tree that inherited the terminal replay; and
* the exact historical damaged combat commit and its verified pre-fight parent.

Callers cannot select a Git ref, commit, path, value, or revision baseline. The
current live state must also be provably descended from the packaged root by a
continuous first-parent Git state history before a forward repair plan exists.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from shinobi_runtime.api.historical_repair_anchor import (
    _ANCHOR as _LEGACY,
    _EXPECTED_STATE_TREE as _LEGACY_DAMAGED_STATE_TREE,
    _combat_elapsed,
    _ensure_anchor_objects,
    _git_list_paths,
    _json_at,
    _player_fatigue,
    _sha,
)
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.tx.errors import GitStageError, StaleRevisionError
from shinobi_runtime.tx.git import (
    CAMPAIGN_TRAILER,
    COMMAND_DIGEST_TRAILER,
    MODE_TRAILER,
    REQUEST_TRAILER,
    REVISION_TRAILER,
    TRANSACTION_TRAILER,
)

PACKAGED_BATTLE_REPAIR_ANCHOR = "jianghu.packaged.black_lance_prebattle.v1"
_META_PATH = "state/meta.json"
_STATE_PREFIX = "state"
_PACKAGED_ROOT = "4d33f3819f7d9d29619fe87df2b00291a16fffcd"
_PACKAGED_STATE_TREE = "701ee943517179d8216d4aec8841c999e4a87d20"
_PACKAGED_REVISION = 7
_PACKAGED_TIME = "SE-0061-09-27T21:16:52"
_PACKAGED_COMBAT_ELAPSED_MS = 109793
_COMBAT_REF = str(_LEGACY["combat_ref"])
_MOVEMENT_REF = "escort_muster:0e52cfa45f5bbea72ba0"


@dataclass(frozen=True)
class PackagedBattleRepairBuild:
    writes: Mapping[str, Optional[bytes]]
    result: Mapping[str, Any]
    affected_refs: tuple[str, ...]


@dataclass(frozen=True)
class _PackagedRepairRequest:
    historical_anchor: str


def _combat_row(git: Any, commit: str) -> Mapping[str, Any]:
    state = _json_at(git, commit, "state/martial-world/combats.json")
    combats = state.get("combats")
    row = combats.get(_COMBAT_REF) if isinstance(combats, Mapping) else None
    if not isinstance(row, Mapping):
        raise OperationError(409, "repair_packaged_provenance_invalid")
    return row


def _verify_legacy_incident(git: Any, campaign_id: str) -> None:
    """Prove the severed historical incident without assuming its old root is live."""
    if campaign_id != _LEGACY["campaign_id"]:
        raise OperationError(409, "repair_packaged_campaign_mismatch")
    _ensure_anchor_objects(git)

    damaged_sha = str(_LEGACY["damaged_commit"])
    restore_sha = str(_LEGACY["restore_commit"])
    damaged = git.get_commit(damaged_sha)
    if (
        damaged.trailers.get(TRANSACTION_TRAILER) != _LEGACY["damaged_transaction_id"]
        or damaged.trailers.get(CAMPAIGN_TRAILER) != campaign_id
        or damaged.trailers.get(REVISION_TRAILER) != str(_LEGACY["damaged_revision"])
        or damaged.trailers.get(MODE_TRAILER) != "gameplay"
        or damaged.trailers.get(REQUEST_TRAILER) != _LEGACY["damaged_request_id"]
        or damaged.trailers.get(COMMAND_DIGEST_TRAILER) != _LEGACY["damaged_command_digest"]
        or _META_PATH not in damaged.paths
        or any(not path.startswith("state/") for path in damaged.paths)
        or git.first_parent(damaged_sha) != restore_sha
        or git.tree_oid(damaged_sha, _STATE_PREFIX) != _LEGACY_DAMAGED_STATE_TREE
    ):
        raise OperationError(409, "repair_packaged_historical_provenance_invalid")

    restore_meta = _json_at(git, restore_sha, _META_PATH)
    if (
        restore_meta.get("campaign_id") != campaign_id
        or restore_meta.get("revision") != _LEGACY["restore_revision"]
        or restore_meta.get("time") != _LEGACY["restore_time"]
        or _combat_elapsed(git, restore_sha) != _LEGACY["restore_combat_elapsed_ms"]
        or _player_fatigue(git, restore_sha) != _LEGACY["restore_player_fatigue_milli"]
    ):
        raise OperationError(409, "repair_packaged_historical_provenance_invalid")


def _verify_packaged_root(git: Any, campaign_id: str) -> None:
    if git.root_commits() != (_PACKAGED_ROOT,):
        raise OperationError(409, "repair_packaged_root_mismatch")
    if git.tree_oid(_PACKAGED_ROOT, _STATE_PREFIX) != _PACKAGED_STATE_TREE:
        raise OperationError(409, "repair_packaged_root_snapshot_mismatch")

    meta = _json_at(git, _PACKAGED_ROOT, _META_PATH)
    combat = _combat_row(git, _PACKAGED_ROOT)
    route = _json_at(git, _PACKAGED_ROOT, "state/martial-world/route-operations.json")
    movements = route.get("movements")
    movement = movements.get(_MOVEMENT_REF) if isinstance(movements, Mapping) else None
    if (
        meta.get("campaign_id") != campaign_id
        or meta.get("revision") != _PACKAGED_REVISION
        or meta.get("time") != _PACKAGED_TIME
        or combat.get("status") != "resolved"
        or combat.get("elapsed_ms") != _PACKAGED_COMBAT_ELAPSED_MS
        or combat.get("winner_side") != "side_a"
        or not isinstance(movement, Mapping)
        or movement.get("combat_ref") != _COMBAT_REF
        or movement.get("status") != "contact_pending"
    ):
        raise OperationError(409, "repair_packaged_root_snapshot_mismatch")


def _first_parent_source_history(git: Any, commit: str) -> str:
    """Return the first parent even when a source-only commit is a merge."""
    completed = git._run_bytes(
        ("rev-list", "--first-parent", "--parents", "-n", "1", commit)
    )
    if completed.returncode:
        raise GitStageError(
            completed.returncode,
            completed.stderr.decode("utf-8", errors="replace"),
        )
    parts = completed.stdout.decode("ascii", errors="strict").strip().split()
    if len(parts) < 2 or parts[0] != commit:
        raise OperationError(409, "repair_packaged_history_invalid")
    return parts[1]


def _verify_current_git_chain(
    *, repository: Any, coordinator: Any, campaign_id: str, expected_revision: int,
) -> tuple[str, ...]:
    """Prove every post-package state mutation from first-parent Git history.

    Source merge commits are allowed only when their state tree is identical to
    their first parent. Any commit that changes state must independently carry
    a valid world-transaction identity and the world revisions must remain
    exactly contiguous from the packaged baseline through ``expected_revision``.
    """
    if expected_revision < _PACKAGED_REVISION:
        raise OperationError(409, "repair_packaged_revision_invalid")
    git = coordinator.git
    head = git.head()
    current = head
    state_commits: list[tuple[int, str]] = []
    seen: set[str] = set()

    while current != _PACKAGED_ROOT:
        if current in seen or len(seen) > 1024:
            raise OperationError(409, "repair_packaged_history_invalid")
        seen.add(current)
        parent = _first_parent_source_history(git, current)
        current_tree = git.tree_oid(current, _STATE_PREFIX)
        parent_tree = git.tree_oid(parent, _STATE_PREFIX)
        if current_tree != parent_tree:
            commit = git.get_commit(current)
            trailers = commit.trailers
            raw_revision = trailers.get(REVISION_TRAILER)
            try:
                revision = int(raw_revision) if raw_revision is not None else -1
            except (TypeError, ValueError):
                revision = -1
            if (
                trailers.get(CAMPAIGN_TRAILER) != campaign_id
                or revision <= _PACKAGED_REVISION
                or trailers.get(MODE_TRAILER) not in {"gameplay", "autonomous", "repair"}
                or not isinstance(trailers.get(TRANSACTION_TRAILER), str)
                or not trailers.get(TRANSACTION_TRAILER)
                or _META_PATH not in commit.paths
                or any(not path.startswith("state/") for path in commit.paths)
            ):
                raise OperationError(409, "repair_packaged_history_invalid")
            state_commits.append((revision, current))
        current = parent

    ordered = list(reversed(state_commits))
    expected = list(range(_PACKAGED_REVISION + 1, expected_revision + 1))
    if [revision for revision, _sha_value in ordered] != expected:
        raise OperationError(409, "repair_packaged_history_incomplete")

    head_paths = set(_git_list_paths(git, head))
    if _META_PATH not in head_paths:
        raise OperationError(409, "repair_packaged_current_state_mismatch")
    for path in sorted(head_paths):
        if repository.digest(path) != _sha(git.read_path_at(head, path)):
            raise OperationError(409, "repair_packaged_current_state_mismatch")
    meta_raw = repository.read_optional_bytes(_META_PATH)
    if meta_raw is None:
        raise OperationError(409, "repair_packaged_current_state_mismatch")
    try:
        meta = json.loads(meta_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationError(409, "repair_packaged_current_state_mismatch") from exc
    if meta.get("campaign_id") != campaign_id or meta.get("revision") != expected_revision:
        raise OperationError(409, "repair_packaged_current_state_mismatch")
    return tuple(sha for _revision, sha in ordered)


def build_packaged_battle_repair(
    *, anchor: str, campaign_id: str, expected_revision: int, repository: Any, coordinator: Any,
) -> PackagedBattleRepairBuild:
    if anchor != PACKAGED_BATTLE_REPAIR_ANCHOR:
        raise OperationError(422, "repair_payload_invalid")
    git = coordinator.git
    _verify_packaged_root(git, campaign_id)
    _verify_legacy_incident(git, campaign_id)
    state_commits = _verify_current_git_chain(
        repository=repository,
        coordinator=coordinator,
        campaign_id=campaign_id,
        expected_revision=expected_revision,
    )

    restore_commit = str(_LEGACY["restore_commit"])
    restore_meta = dict(_json_at(git, restore_commit, _META_PATH))
    restore_paths = set(_git_list_paths(git, restore_commit))
    current_paths = set(_git_list_paths(git, git.head()))
    if _META_PATH not in restore_paths:
        raise OperationError(409, "repair_packaged_historical_provenance_invalid")

    writes: dict[str, Optional[bytes]] = {}
    for path in sorted(restore_paths | current_paths):
        desired = git.read_path_at(restore_commit, path) if path in restore_paths else None
        if path == _META_PATH:
            restore_meta["revision"] = expected_revision + 1
            desired = (json.dumps(restore_meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if repository.read_optional_bytes(path) != desired:
            writes[path] = desired
    if _META_PATH not in writes:
        raise OperationError(409, "repair_packaged_historical_provenance_invalid")

    result = {
        "repair_kind": "forward_packaged_historical_battle_restore",
        "historical_anchor": PACKAGED_BATTLE_REPAIR_ANCHOR,
        "committed_revision": expected_revision + 1,
        "packaged_root_revision": _PACKAGED_REVISION,
        "packaged_root": _PACKAGED_ROOT,
        "post_package_state_commit_count": len(state_commits),
        "restored_state_revision": _LEGACY["restore_revision"],
        "restored_world_time": _LEGACY["restore_time"],
        "restored_combat_elapsed_ms": _LEGACY["restore_combat_elapsed_ms"],
        "historical_damaged_transaction_id": _LEGACY["damaged_transaction_id"],
        "historical_damaged_commit": _LEGACY["damaged_commit"],
        "restore_commit": restore_commit,
        "repaired_path_count": len(writes),
        "provenance_source": "fixed_packaged_root_plus_first_parent_state_chain_plus_verified_historical_parent",
    }
    return PackagedBattleRepairBuild(
        writes=writes,
        result=result,
        affected_refs=tuple(sorted(writes)),
    )


def _validate_command(self: Any, command: CommandEnvelope, *, require_revision: bool) -> _PackagedRepairRequest:
    from shinobi_runtime.api.repair import REPAIR_COMMAND_TYPE, REPAIR_MODE

    if command.mode != REPAIR_MODE or command.command_type != REPAIR_COMMAND_TYPE:
        raise OperationError(403, "repair_mode_required")
    if command.actor_id not in self.operations.allowed_actor_ids:
        raise OperationError(403, "actor_not_allowed")
    if set(command.payload) != {"historical_anchor"} or command.payload.get("historical_anchor") != PACKAGED_BATTLE_REPAIR_ANCHOR:
        raise OperationError(422, "repair_payload_invalid")
    try:
        self.repository.require_campaign(command.campaign_id, _META_PATH)
        if require_revision:
            self.repository.require_revision(command.expected_revision, _META_PATH)
    except StaleRevisionError as exc:
        raise OperationError(409, "stale_revision") from exc
    except (TypeError, ValueError) as exc:
        raise OperationError(409, "repair_campaign_mismatch") from exc
    return _PackagedRepairRequest(PACKAGED_BATTLE_REPAIR_ANCHOR)


def install_packaged_battle_repair_anchor() -> None:
    """Compose this one closed packaged-battle restore into repair service."""
    from shinobi_runtime.api import repair as repair_module

    service_type = repair_module.CampaignRepairService
    if getattr(service_type, "_packaged_battle_anchor_installed", False):
        return
    original_require_base = service_type._require_base
    original_build = service_type._build

    def require_base(self: Any, command: CommandEnvelope, *, require_revision: bool = True):
        if set(command.payload) == {"historical_anchor"}:
            return _validate_command(self, command, require_revision=require_revision)
        return original_require_base(self, command, require_revision=require_revision)

    def build(self: Any, command: CommandEnvelope):
        if set(command.payload) != {"historical_anchor"}:
            return original_build(self, command)
        request = self._require_base(command)
        self._require_fresh_deployment()
        repaired = build_packaged_battle_repair(
            anchor=request.historical_anchor,
            campaign_id=command.campaign_id,
            expected_revision=command.expected_revision,
            repository=self.repository,
            coordinator=self.coordinator,
        )
        return repair_module._RepairPlan(
            transaction_id="tx.repair." + command.digest,
            created_at=command.submitted_at,
            writes=repaired.writes,
            result=repaired.result,
            affected_refs=repaired.affected_refs,
        )

    service_type._require_base = require_base
    service_type._build = build
    service_type._packaged_battle_anchor_installed = True


__all__ = [
    "PACKAGED_BATTLE_REPAIR_ANCHOR",
    "PackagedBattleRepairBuild",
    "build_packaged_battle_repair",
    "install_packaged_battle_repair_anchor",
]
