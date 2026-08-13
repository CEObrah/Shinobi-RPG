"""Production preview validation parity with transaction execution.

A READY preview is an attestation used by the MCP write boundary.  It must not
mean only that command planning succeeded while deterministic schema, template,
or domain validation is deferred until execute.  This installer wraps the
production operations class so every READY preview dry-runs the exact planned
manifest through the same after-image validators used before persistence.

The dry run is read-only: it constructs a TransactionManifest and StagedOverlay
but never prepares WAL state, mutates campaign owners, stages Git, or writes a
receipt.  Execution still repeats validation under its transaction lock, so a
real state change between preview and execution continues to fail safely.
"""
from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandPlan, CommandRejectedError
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.errors import (
    DirtyRepositoryError,
    LockUnavailableError,
    RecoveryError,
    StaleRevisionError,
)

_INSTALLED = False


def _validate_ready_plan(operations: Any, command: Any) -> None:
    """Dry-run the exact production plan through execution-equivalent validators."""

    with operations._locked():
        operations._require_command_base(command)
        operations.coordinator.git.assert_pristine()
        before = operations._read_fingerprint()
        plan = operations.command_planner.plan(command)
        if not isinstance(plan, CommandPlan):
            raise OperationError(503, "planner_plan_invalid")
        operations._require_read_only(before, "planner_mutated_campaign")

        manifest = operations.coordinator.planner.plan(
            command,
            transaction_id=plan.transaction_id,
            created_at=plan.created_at,
            writes=plan.writes,
        )
        overlay = StagedOverlay(operations.repository, manifest)

        try:
            if operations.schema_validator is not None:
                operations.schema_validator.validate_overlay(overlay, manifest.paths)
        except (TypeError, ValueError) as exc:
            raise OperationError(409, "preview_schema_validation_failed") from exc

        try:
            if operations.template_validator is not None:
                operations.template_validator.validate_overlay(overlay, manifest.paths)
        except (TypeError, ValueError) as exc:
            raise OperationError(409, "preview_template_validation_failed") from exc

        try:
            plan.validator(overlay, manifest)
        except CommandRejectedError as exc:
            raise OperationError(422, exc.code) from exc
        except (TypeError, ValueError) as exc:
            raise OperationError(409, "preview_plan_validation_failed") from exc

        operations._require_read_only(before, "preview_validation_mutated_campaign")


def install_preview_validation() -> None:
    """Install production READY-preview validation once per process."""

    global _INSTALLED
    if _INSTALLED:
        return

    from shinobi_runtime.api import campaign_stable_operations as stable_module

    cls = stable_module.RouteAwareCampaignOperations
    original = cls.preview_command
    if getattr(original, "_shinobi_exact_validation_parity", False):
        _INSTALLED = True
        return

    def preview_command(self: Any, command: Any) -> Mapping[str, Any]:
        preview = original(self, command)
        if preview.get("status") != "ready":
            return preview
        try:
            _validate_ready_plan(self, command)
        except OperationError:
            raise
        except StaleRevisionError as exc:
            raise OperationError(409, "stale_revision") from exc
        except LockUnavailableError as exc:
            raise OperationError(503, "campaign_writer_busy") from exc
        except (DirtyRepositoryError, RecoveryError) as exc:
            raise OperationError(503, "campaign_unavailable") from exc
        return preview

    preview_command._shinobi_exact_validation_parity = True  # type: ignore[attr-defined]
    cls.preview_command = preview_command
    _INSTALLED = True


__all__ = ["install_preview_validation", "_validate_ready_plan"]
