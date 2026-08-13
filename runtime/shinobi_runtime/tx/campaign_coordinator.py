"""Production transaction coordinator integrity guard for repaired campaigns."""

from __future__ import annotations

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.tx.coordinator import TransactionCoordinator as _BaseTransactionCoordinator
from shinobi_runtime.tx.errors import IdempotencyConflictError, RecoveryError
from shinobi_runtime.tx.invalidations import (
    command_matches_invalidated_request,
    load_transaction_invalidations,
    receipt_is_invalidated,
)


_MAX_RECEIPTS = 4096


class TransactionCoordinator(_BaseTransactionCoordinator):
    """Fail closed on unexplained future receipts while honoring exact repairs."""

    def _invalidations(self):
        try:
            return load_transaction_invalidations(self.repository)
        except (TypeError, ValueError) as exc:
            raise RecoveryError("transaction invalidation registry is invalid") from exc

    def _assert_invalidated_request_not_retried(self, command: CommandEnvelope) -> None:
        if command_matches_invalidated_request(command, self._invalidations()):
            raise IdempotencyConflictError(
                "request ID belongs to an explicitly invalidated campaign transaction; "
                "submit the intended action with a new request ID"
            )

    def _assert_receipt_integrity(self) -> None:
        invalidations = self._invalidations()
        try:
            campaign_id = self.repository.campaign_id(self.meta_path)
            current_revision = self.repository.current_revision(self.meta_path)
            paths = sorted(self.receipts.directory.glob("*.json"))
        except (OSError, TypeError, ValueError) as exc:
            raise RecoveryError("idempotency receipt integrity check failed") from exc
        if len(paths) > _MAX_RECEIPTS:
            raise RecoveryError(
                "idempotency receipt integrity scan exceeds bounded capacity"
            )

        for path in paths:
            try:
                receipt = self.receipts._read(path)
            except (OSError, TypeError, ValueError) as exc:
                raise RecoveryError("corrupt idempotency receipt detected") from exc
            if receipt.campaign_id != campaign_id:
                continue
            if receipt.committed_revision <= current_revision:
                continue
            if receipt_is_invalidated(
                receipt,
                invalidations,
                current_revision=current_revision,
            ):
                continue
            raise RecoveryError(
                "idempotency receipt claims a future campaign revision without "
                "an exact registered repair invalidation"
            )

    def recover(self):
        decisions = super().recover()
        self._assert_receipt_integrity()
        return decisions

    def lookup_receipt(self, command: CommandEnvelope):
        self._assert_invalidated_request_not_retried(command)
        self._assert_receipt_integrity()
        return super().lookup_receipt(command)

    def execute(self, command: CommandEnvelope, *args, **kwargs):
        self._assert_invalidated_request_not_retried(command)
        self._assert_receipt_integrity()
        return super().execute(command, *args, **kwargs)


__all__ = ["TransactionCoordinator"]
