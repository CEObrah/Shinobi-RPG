"""Production transaction coordinator integrity guard."""

from __future__ import annotations

from shinobi_runtime.tx.coordinator import TransactionCoordinator as _BaseTransactionCoordinator
from shinobi_runtime.tx.errors import RecoveryError


class TransactionCoordinator(_BaseTransactionCoordinator):
    """Fail closed when immutable receipts are ahead of campaign state."""

    def _assert_receipt_integrity(self) -> None:
        """Use the durable receipt high-water mark for the normal fast path.

        Current campaign state never interprets future receipts through a
        rollback/invalidation ledger. If exact receipts exist above current
        state, the repository/runtime volume is inconsistent and gameplay
        writes fail closed.
        """
        try:
            campaign_id = self.repository.campaign_id(self.meta_path)
            current_revision = self.repository.current_revision(self.meta_path)
            maximum = self.receipts.campaign_max_revision(campaign_id)
        except (OSError, TypeError, ValueError) as exc:
            raise RecoveryError("idempotency receipt integrity check failed") from exc
        if maximum is None or maximum <= current_revision:
            return
        try:
            future = tuple(self.receipts.iter_campaign_receipts_above(campaign_id, current_revision))
            if future:
                raise RecoveryError("idempotency receipt claims a future campaign revision")
            # The high-water index may overestimate only if receipt publication
            # was interrupted; rebuild that small index in this exceptional path.
            self.receipts.rebuild_index()
            rebuilt = self.receipts.campaign_max_revision(campaign_id)
            if rebuilt is not None and rebuilt > current_revision:
                raise RecoveryError("idempotency receipt claims a future campaign revision")
        except RecoveryError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise RecoveryError("corrupt idempotency receipt detected") from exc

    def recover(self):
        decisions = super().recover()
        self._assert_receipt_integrity()
        return decisions

    def lookup_receipt(self, command):
        self._assert_receipt_integrity()
        return super().lookup_receipt(command)

    def execute(self, command, *args, **kwargs):
        self._assert_receipt_integrity()
        return super().execute(command, *args, **kwargs)


__all__ = ["TransactionCoordinator"]
