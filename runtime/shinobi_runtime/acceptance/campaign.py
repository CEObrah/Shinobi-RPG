"""Archive-mode production campaign acceptance support.

This module executes the real repository-backed command planner, manifest
planner, staged validators, and atomic persister against disposable campaign
copies.  It deliberately does not emulate Git/WAL.  Those guarantees are
covered by the transaction-coordinator suite; this harness exists to prove
that migrated campaign data can actually exercise production semantic
commands when only a ZIP checkout is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Optional, Tuple

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.store import (
    RegisteredSchemaValidator,
    RegisteredTemplateValidator,
    RepositoryStore,
    StagedOverlay,
    content_root,
)
from shinobi_runtime.tx.canonical import thaw_json
from shinobi_runtime.tx.manifest import TransactionPlanner
from shinobi_runtime.tx.persistence import AtomicManifestPersister


class CountingRepositoryStore(RepositoryStore):
    """Repository store that records bounded unique reads for locality metrics."""

    def __init__(self, root: object) -> None:
        super().__init__(root)
        self._reads: set[str] = set()

    def read_optional_bytes(self, relative_path: object) -> Optional[bytes]:
        normalized = str(relative_path)
        self._reads.add(normalized)
        return super().read_optional_bytes(relative_path)

    def reset_reads(self) -> None:
        self._reads.clear()

    @property
    def reads(self) -> Tuple[str, ...]:
        return tuple(sorted(self._reads))

    @property
    def state_reads(self) -> Tuple[str, ...]:
        return tuple(path for path in self.reads if path.startswith("state/"))


@dataclass(frozen=True)
class ArchiveExecutionReceipt:
    command_type: str
    request_id: str
    base_revision: int
    target_revision: int
    planning_state_reads: Tuple[str, ...]
    write_paths: Tuple[str, ...]
    result: Mapping[str, Any]
    before_state_root: str
    after_state_root: str
    elapsed_ms: float

    @property
    def planning_read_count(self) -> int:
        return len(self.planning_state_reads)

    @property
    def write_count(self) -> int:
        return len(self.write_paths)


class ArchiveCampaignExecutor:
    """Execute production command plans against a disposable archive checkout."""

    def __init__(
        self,
        root: object,
        *,
        hash_each_transaction: bool = True,
        validate_contracts: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.hash_each_transaction = bool(hash_each_transaction)
        self.validate_contracts = bool(validate_contracts)
        self.repository = CountingRepositoryStore(self.root)
        self.planner = RepositoryCommandPlanner(self.repository)
        self.manifest_planner = TransactionPlanner(self.repository)
        self.persister = AtomicManifestPersister(self.repository)
        self.schema_validator = RegisteredSchemaValidator.optional(self.repository)
        self.template_validator = RegisteredTemplateValidator.optional(self.repository)
        self._sequence = 0

    def _envelope(
        self,
        command_type: str,
        payload: Mapping[str, Any],
        *,
        actor_id: Optional[str],
        request_id: Optional[str],
        mode: str,
    ) -> CommandEnvelope:
        meta = self.repository.read_json("state/meta.json")
        if not isinstance(meta, Mapping):
            raise ValueError("campaign meta is invalid")
        self._sequence += 1
        player_id = meta.get("player_id")
        chosen_actor = actor_id or player_id
        if not isinstance(chosen_actor, str) or not chosen_actor:
            raise ValueError("campaign actor is unavailable")
        rid = request_id or f"acceptance.{self._sequence:06d}.{command_type}"
        return CommandEnvelope(
            campaign_id=meta.get("campaign_id"),
            request_id=rid,
            actor_id=chosen_actor,
            command_type=command_type,
            expected_revision=meta.get("revision"),
            submitted_at="2026-08-09T00:00:00Z",
            payload=payload,
            mode=mode,
        )

    def execute(
        self,
        command_type: str,
        payload: Mapping[str, Any],
        *,
        actor_id: Optional[str] = None,
        request_id: Optional[str] = None,
        mode: str = "gameplay",
    ) -> ArchiveExecutionReceipt:
        command = self._envelope(
            command_type,
            payload,
            actor_id=actor_id,
            request_id=request_id,
            mode=mode,
        )
        before_revision = command.expected_revision
        before_root = (
            content_root(self.root, include_roots=("state",)).root_sha256
            if self.hash_each_transaction
            else ""
        )

        self.repository.reset_reads()
        started = perf_counter()
        plan = self.planner.plan(command)
        planning_state_reads = self.repository.state_reads

        manifest = self.manifest_planner.plan(
            command,
            plan.transaction_id,
            plan.created_at,
            plan.writes,
        )
        overlay = StagedOverlay(self.repository, manifest)
        plan.validator(overlay, manifest)
        if self.validate_contracts and self.schema_validator is not None:
            self.schema_validator.validate_overlay(overlay, overlay.changed_paths)
        if self.validate_contracts and self.template_validator is not None:
            self.template_validator.validate_overlay(overlay, overlay.changed_paths)

        applied = self.persister.apply(manifest)
        for mutation in manifest.mutations:
            if self.repository.digest(mutation.path) != mutation.after_sha256:
                raise AssertionError(f"readback mismatch after archive execution: {mutation.path}")
        after_revision = self.repository.current_revision()
        if after_revision != before_revision + 1:
            raise AssertionError("archive execution did not advance exactly one revision")
        after_root = (
            content_root(self.root, include_roots=("state",)).root_sha256
            if self.hash_each_transaction
            else ""
        )
        if self.hash_each_transaction and after_root == before_root:
            raise AssertionError("gameplay transaction did not change campaign state root")

        return ArchiveExecutionReceipt(
            command_type=command_type,
            request_id=command.request_id,
            base_revision=before_revision,
            target_revision=after_revision,
            planning_state_reads=planning_state_reads,
            write_paths=tuple(applied),
            result=thaw_json(plan.result),
            before_state_root=before_root,
            after_state_root=after_root,
            elapsed_ms=(perf_counter() - started) * 1000.0,
        )
