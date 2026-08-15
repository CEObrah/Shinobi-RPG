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
        """Use the durable receipt high-water mark for the normal fast path.

        Historical receipts are scanned only when their indexed maximum lies
        ahead of mutable campaign state, which is the unusual rollback/repair
        case that needs exact invalidation verification.  Receipt cardinality
        is never a campaign-validity condition.
        """

        invalidations = self._invalidations()
        try:
            campaign_id = self.repository.campaign_id(self.meta_path)
            current_revision = self.repository.current_revision(self.meta_path)
            maximum = self.receipts.campaign_max_revision(campaign_id)
        except (OSError, TypeError, ValueError) as exc:
            raise RecoveryError("idempotency receipt integrity check failed") from exc

        if maximum is None or maximum <= current_revision:
            return

        try:
            future_receipts = self.receipts.iter_campaign_receipts_above(
                campaign_id, current_revision
            )
            found_future = False
            for receipt in future_receipts:
                found_future = True
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
            if not found_future:
                # A crash may safely leave an overestimated high-water mark if
                # it occurs after indexing but before the immutable receipt is
                # linked.  Rebuild only in this exceptional path.
                self.receipts.rebuild_index()
        except RecoveryError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise RecoveryError("corrupt idempotency receipt detected") from exc

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
