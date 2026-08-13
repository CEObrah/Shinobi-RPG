"""Repair-aware OOC diagnostics for the production campaign runtime."""

from __future__ import annotations

from typing import Optional

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


class RepositoryOocAudit(_BaseRepositoryOocAudit):
    """Classify exact repaired receipts separately from unexplained future ones."""

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

        files, truncated = _bounded_files(directory, self.max_runtime_records)
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
