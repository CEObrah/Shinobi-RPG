"""Keep exact-team routine training from freezing behind a shared development cursor.

The development bank cursor records the latest settled character-development
boundary. It is not an exclusive lease over elapsed wall-clock time. Exact-team
routine training has its own durable authorities: the scheduled team review,
exact session ledger, mission/assignment gates, readiness/location checks, and
global weekly-load/recovery guard.

Historically, :mod:`living_world_training` treated the shared cursor as a hard
prerequisite for every weekly team review. A stale cursor, or a cursor advanced
inside a compacted review window by another development domain, therefore made
a lawful team permanently unable to train. This compatibility install removes
only that cross-domain veto. It does not backfill missed work and it does not
weaken any training-capacity, recovery, mission, health, location, or instructor
rule. Historical debt must be settled by an explicit guarded repair.
"""
from __future__ import annotations

from typing import Optional

from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False


def _team_review_cursor_skip(_cursor: CampaignTime, _interval_start: CampaignTime) -> Optional[str]:
    """Return no shared-cursor veto for an otherwise lawful exact-team review."""
    return None


def install_team_training_cursor_reconciliation() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import living_world_training as module

    module._development_cursor_skip = _team_review_cursor_skip
    _INSTALLED = True


__all__ = [
    "install_team_training_cursor_reconciliation",
    "_team_review_cursor_skip",
]
