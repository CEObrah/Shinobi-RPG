"""Stable error boundaries for scheduled autonomous review extensions.

Living-world extensions are composed from several optional production mixins.
A raw ``TypeError``/``ValueError`` escaping one of those extensions must never be
reported by the MCP transport as malformed player command input.  Convert only
unexpected implementation-shape failures here; explicit domain rejections keep
their original stable codes.
"""
from __future__ import annotations

from typing import Any

from shinobi_runtime.api.contracts import CommandRejectedError


class AutonomyErrorBoundaryMixin:
    """Translate unexpected autonomy implementation errors into domain failures."""

    def _apply_autonomous_decision(self, *args: Any, **kwargs: Any):
        try:
            return super()._apply_autonomous_decision(*args, **kwargs)
        except CommandRejectedError:
            raise
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("faction_autonomy_resolution_invalid") from exc

    def _apply_team_autonomy_review(self, *args: Any, **kwargs: Any):
        try:
            return super()._apply_team_autonomy_review(*args, **kwargs)
        except CommandRejectedError:
            raise
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("team_autonomy_resolution_invalid") from exc


__all__ = ["AutonomyErrorBoundaryMixin"]
