"""Repair-aware OOC diagnostics for the production campaign runtime."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from shinobi_runtime.api.ooc import (
    RepositoryOocAudit as _BaseRepositoryOocAudit,
    _bounded_files,
    _mapping,
)
from shinobi_runtime.tx.invalidations import (
    load_transaction_invalidations,
    receipt_is_invalidated,
)
from shinobi_runtime.tx.receipts import IdempotencyReceipt

_TERMINAL_PRESSURE_STATUSES = frozenset(
    ("completed", "resolved", "failed", "cancelled", "abandoned", "superseded")
)


def _has_material_pressure_basis(pressure: Mapping[str, Any]) -> bool:
    for field in ("source_refs", "evidence_refs", "actor_refs", "resource_refs", "material_refs"):
        values = pressure.get(field)
        if isinstance(values, list) and any(isinstance(value, str) and value for value in values):
            return True
    for field in ("current_step", "operation_ref", "mission_ref", "conflict_ref", "claim_ref"):
        value = pressure.get(field)
        if isinstance(value, str) and value:
            return True
        if isinstance(value, Mapping) and value:
            return True
    return False


class RepositoryOocAudit(_BaseRepositoryOocAudit):
    """Classify repaired receipts and distinguish latent canon clocks from arcs."""

    def _audit_pressures(self, report, world_time, scheduler_hosts) -> None:
        super()._audit_pressures(report, world_time, scheduler_hosts)
        registry = self._read_repository_json(
            report, "state/canon/pressures.json", "canon_pressure_registry"
        )
        pressures = _mapping(registry.get("pressures")) if registry is not None else None
        if pressures is None:
            return
        active = 0
        actionable = 0
        latent_unsourced = 0
        for pressure in pressures.values():
            if not isinstance(pressure, Mapping):
                continue
            status = pressure.get("status")
            if isinstance(status, str) and status in _TERMINAL_PRESSURE_STATUSES:
                continue
            active += 1
            if _has_material_pressure_basis(pressure):
                actionable += 1
            else:
                source_refs = pressure.get("source_refs")
                if isinstance(source_refs, list) and not source_refs:
                    latent_unsourced += 1
        report.diagnostic(
            "canon_pressure_actionability:summary "
            f"active={active} actionable={actionable} latent_unsourced={latent_unsourced}"
        )
        if active and actionable == 0:
            report.suggestion(
                "treat_unsourced_canon_pressure_clocks_as_latent_not_world_arc_evidence"
            )

    def _audit_receipts(self, report, campaign_revision: Optional[int]) -> None:
        directory = self.runtime_root / "receipts"
        if not directory.is_dir():
            report.diagnostic("receipts:unavailable")
            report.suggestion("mount_persistent_runtime_storage_for_wal_and_receipts")
            return
        try:
            invalidations = load_transaction_invalidations(self.repository)
        except (TypeError, ValueError):
            report.diagnostic("transaction_invalidations:invalid")
            report.suggestion("repair_transaction_invalidation_registry_before_gameplay")
            invalidations = ()

        files, truncated = _bounded_files(
            directory,
            self.max_runtime_records,
            receipt_only=True,
        )
        invalid = 0
        future_revision = 0
        invalidated_future = 0
        highest_revision: Optional[int] = None
        for path in files:
            record = _mapping(self._read_json_file(path))
            if record is None:
                invalid += 1
                continue
            try:
                receipt = IdempotencyReceipt.from_record(record)
            except (TypeError, ValueError):
                invalid += 1
                continue
            highest_revision = (
                receipt.committed_revision
                if highest_revision is None
                else max(highest_revision, receipt.committed_revision)
            )
            if campaign_revision is None or receipt.committed_revision <= campaign_revision:
                continue
            if receipt_is_invalidated(
                receipt,
                invalidations,
                current_revision=campaign_revision,
            ):
                invalidated_future += 1
            else:
                future_revision += 1

        report.diagnostic(
            "receipts:summary "
            f"scanned={len(files)} invalid={invalid} future_revision={future_revision} "
            f"invalidated_future={invalidated_future} "
            f"highest_revision={highest_revision if highest_revision is not None else 'none'} "
            f"scan_budget_exceeded={str(truncated).lower()}"
        )
        if invalid or future_revision:
            report.suggestion("investigate_receipt_integrity_before_accepting_gameplay_writes")
        if invalidated_future:
            report.diagnostic(
                "receipts:registered_campaign_repair_invalidations="
                f"{invalidated_future}"
            )
        if truncated:
            report.suggestion("archive_receipts_only_under_a_reviewed_idempotency_retention_policy")


__all__ = ["RepositoryOocAudit"]
