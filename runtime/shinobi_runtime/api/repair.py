"""Privileged forward-only repair of one committed campaign transaction.

A repair never rewrites Git history and never accepts arbitrary repository refs
or paths from a caller.  The damaged transaction id is resolved through its
immutable Shinobi commit trailers; only that commit's exact ``state/`` paths are
restored from its first parent, while ``state/meta.json`` advances to a new
revision.  Current source files are therefore preserved.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from shinobi_runtime.api.operations import CampaignOperations, OperationError, PlanStateChangedError
from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.deployment_freshness import inspect_deployment_freshness
from shinobi_runtime.tx.canonical import thaw_json
from shinobi_runtime.tx.errors import (
    DirtyRepositoryError,
    GitStageError,
    IdempotencyConflictError,
    LockUnavailableError,
    RecoveryError,
    StaleRevisionError,
    TransactionError,
)
from shinobi_runtime.tx.git import (
    CAMPAIGN_TRAILER,
    MODE_TRAILER,
    REVISION_TRAILER,
    TRANSACTION_TRAILER,
)

REPAIR_COMMAND_TYPE = "campaign_forward_repair"
REPAIR_MODE = "repair"
_META_PATH = "state/meta.json"


@dataclass(frozen=True)
class _RepairPlan:
    transaction_id: str
    created_at: str
    writes: Mapping[str, Optional[bytes]]
    result: Mapping[str, Any]
    affected_refs: tuple[str, ...]


class CampaignRepairService:
    """Execute one bounded forward repair through the normal transaction coordinator."""

    def __init__(self, operations: CampaignOperations) -> None:
        self.operations = operations
        self.repository = operations.repository
        self.coordinator = operations.coordinator

    def _require_base(self, command: CommandEnvelope) -> str:
        if command.mode != REPAIR_MODE or command.command_type != REPAIR_COMMAND_TYPE:
            raise OperationError(403, "repair_mode_required")
        if command.actor_id not in self.operations.allowed_actor_ids:
            raise OperationError(403, "actor_not_allowed")
        if set(command.payload) != {"damaged_transaction_id"}:
            raise OperationError(422, "repair_payload_invalid")
        damaged_transaction_id = command.payload.get("damaged_transaction_id")
        if (
            not isinstance(damaged_transaction_id, str)
            or not damaged_transaction_id.startswith("tx.")
            or len(damaged_transaction_id) > 160
        ):
            raise OperationError(422, "repair_payload_invalid")
        try:
            self.repository.require_campaign(command.campaign_id, _META_PATH)
            self.repository.require_revision(command.expected_revision, _META_PATH)
        except ValueError as exc:
            if "revision" in str(exc).lower():
                raise OperationError(409, "stale_revision") from exc
            raise OperationError(409, "repair_campaign_mismatch") from exc
        return damaged_transaction_id

    def _require_fresh_deployment(self) -> None:
        freshness = inspect_deployment_freshness(self.repository.root)
        if freshness.production and not freshness.healthy:
            raise OperationError(503, "deployment_source_stale")

    @staticmethod
    def _revision_trailer(record: Any) -> int:
        raw = record.trailers.get(REVISION_TRAILER)
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise OperationError(409, "repair_provenance_invalid") from exc
        if value < 0:
            raise OperationError(409, "repair_provenance_invalid")
        return value

    def _build(self, command: CommandEnvelope) -> _RepairPlan:
        damaged_transaction_id = self._require_base(command)
        self._require_fresh_deployment()
        git = self.coordinator.git
        head = git.head()
        damaged = git.find_transaction_commit(damaged_transaction_id)
        if damaged is None:
            raise OperationError(404, "repair_transaction_not_found")
        if (
            damaged.trailers.get(TRANSACTION_TRAILER) != damaged_transaction_id
            or damaged.trailers.get(CAMPAIGN_TRAILER) != command.campaign_id
            or damaged.trailers.get(MODE_TRAILER) not in {"gameplay", "autonomous"}
            or self._revision_trailer(damaged) != command.expected_revision
        ):
            raise OperationError(409, "repair_provenance_invalid")
        if not git.is_ancestor(damaged.commit_hash, head):
            raise OperationError(409, "repair_provenance_invalid")
        if git.tree_oid(head, "state") != git.tree_oid(damaged.commit_hash, "state"):
            raise OperationError(409, "repair_base_changed")
        restore_commit = git.first_parent(damaged.commit_hash)
        restore_meta_raw = git.read_path_at(restore_commit, _META_PATH)
        damaged_meta_raw = git.read_path_at(damaged.commit_hash, _META_PATH)
        if restore_meta_raw is None or damaged_meta_raw is None:
            raise OperationError(409, "repair_provenance_invalid")
        try:
            restore_meta = json.loads(restore_meta_raw.decode("utf-8"))
            damaged_meta = json.loads(damaged_meta_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperationError(409, "repair_provenance_invalid") from exc
        if (
            not isinstance(restore_meta, dict)
            or not isinstance(damaged_meta, dict)
            or restore_meta.get("campaign_id") != command.campaign_id
            or damaged_meta.get("campaign_id") != command.campaign_id
            or restore_meta.get("revision") != command.expected_revision - 1
            or damaged_meta.get("revision") != command.expected_revision
            or _META_PATH not in damaged.paths
            or any(not path.startswith("state/") for path in damaged.paths)
        ):
            raise OperationError(409, "repair_provenance_invalid")

        writes: dict[str, Optional[bytes]] = {}
        for path in damaged.paths:
            desired = git.read_path_at(restore_commit, path)
            if path == _META_PATH:
                repaired_meta = dict(restore_meta)
                repaired_meta["revision"] = command.expected_revision + 1
                desired = (
                    json.dumps(repaired_meta, ensure_ascii=False, indent=2) + "\n"
                ).encode("utf-8")
            current = self.repository.read_optional_bytes(path)
            if current != desired:
                writes[path] = desired
        if _META_PATH not in writes:
            raise OperationError(409, "repair_provenance_invalid")

        result = {
            "repair_kind": "forward_transaction_repair",
            "damaged_revision": command.expected_revision,
            "restored_state_revision": command.expected_revision - 1,
            "committed_revision": command.expected_revision + 1,
            "restored_world_time": restore_meta.get("time"),
            "damaged_transaction_id": damaged_transaction_id,
            "damaged_commit": damaged.commit_hash,
            "restore_commit": restore_commit,
            "repaired_path_count": len(writes),
        }
        return _RepairPlan(
            transaction_id="tx.repair." + command.digest,
            created_at=command.submitted_at,
            writes=writes,
            result=result,
            affected_refs=tuple(sorted(writes)),
        )

    def preview(self, command: CommandEnvelope) -> Mapping[str, Any]:
        try:
            with self.operations._locked():
                self.coordinator.git.assert_pristine()
                before = self.operations._read_fingerprint()
                plan = self._build(command)
                self.operations._require_read_only(before, "repair_preview_mutated_campaign")
        except OperationError:
            raise
        except (LockUnavailableError, DirtyRepositoryError, GitStageError) as exc:
            raise OperationError(503, "campaign_unavailable") from exc
        return {
            "status": "ready",
            "code": "campaign_forward_repair_ready",
            "target_revision": command.expected_revision + 1,
            "affected_refs": list(plan.affected_refs),
            "result": dict(plan.result),
        }

    @staticmethod
    def _receipt_response(status: str, receipt: Any) -> Mapping[str, Any]:
        return {
            "status": status,
            "request_id": receipt.request_id,
            "transaction_id": receipt.transaction_id,
            "campaign_id": receipt.campaign_id,
            "committed_revision": receipt.committed_revision,
            "committed_at": receipt.committed_at,
            "result": thaw_json(receipt.result),
        }

    def lookup_receipt(self, command: CommandEnvelope) -> Optional[Mapping[str, Any]]:
        self._require_base(command)
        try:
            existing = self.coordinator.lookup_receipt(command)
        except IdempotencyConflictError as exc:
            raise OperationError(409, "idempotency_conflict") from exc
        return None if existing is None else self._receipt_response("duplicate", existing)

    def execute(self, command: CommandEnvelope) -> Mapping[str, Any]:
        self._require_base(command)
        self._require_fresh_deployment()
        try:
            existing = self.coordinator.lookup_receipt(command)
            if existing is not None:
                return self._receipt_response("duplicate", existing)
            with self.operations._locked():
                self.coordinator.git.assert_pristine()
                before = self.operations._read_fingerprint()
                plan = self._build(command)
                self.operations._require_read_only(before, "repair_planner_mutated_campaign")
                planned_head, planned_root = before

            def guarded(overlay: Any, manifest: Any) -> None:
                if (
                    self.coordinator.git.head() != planned_head
                    or self.operations.state_roots.read(planned_head).root_sha256 != planned_root
                ):
                    raise PlanStateChangedError()
                if manifest.mode != REPAIR_MODE or tuple(manifest.paths) != tuple(sorted(plan.writes)):
                    raise PlanStateChangedError()
                if self.operations.schema_validator is not None:
                    self.operations.schema_validator.validate_overlay(overlay, manifest.paths)
                if self.operations.template_validator is not None:
                    self.operations.template_validator.validate_overlay(overlay, manifest.paths)
                repaired_meta = overlay.read_json(_META_PATH)
                if (
                    repaired_meta.get("campaign_id") != command.campaign_id
                    or repaired_meta.get("revision") != command.expected_revision + 1
                ):
                    raise ValueError("repair meta after-image is invalid")

            execution = self.coordinator.execute(
                command,
                transaction_id=plan.transaction_id,
                created_at=plan.created_at,
                writes=plan.writes,
                result=dict(plan.result),
                validator=guarded,
            )
        except OperationError:
            raise
        except StaleRevisionError as exc:
            raise OperationError(409, "stale_revision") from exc
        except IdempotencyConflictError as exc:
            raise OperationError(409, "idempotency_conflict") from exc
        except PlanStateChangedError as exc:
            raise OperationError(409, "planned_state_changed") from exc
        except (LockUnavailableError, DirtyRepositoryError, RecoveryError, GitStageError) as exc:
            raise OperationError(503, "campaign_unavailable") from exc
        except TransactionError as exc:
            raise OperationError(409, "transaction_rejected") from exc
        return self._receipt_response(execution.status, execution.receipt)


__all__ = ["CampaignRepairService", "REPAIR_COMMAND_TYPE", "REPAIR_MODE"]
