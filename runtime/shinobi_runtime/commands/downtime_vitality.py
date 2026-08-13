"""Extend downtime stopping criteria with explicitly delivered reports."""
from __future__ import annotations

from functools import wraps
from typing import Any, Mapping

_INSTALLED = False


def install_downtime_vitality() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import downtime_until_event as module

    original = module._meaningful
    if getattr(original, "_delivered_report_vitality", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(result: Mapping[str, Any]) -> bool:
        if original(result):
            return True
        actions = result.get("autonomous_actions")
        if not isinstance(actions, list):
            return False
        return any(
            isinstance(action, Mapping)
            and isinstance(action.get("player_report_deliveries"), list)
            and any(isinstance(row, Mapping) for row in action["player_report_deliveries"])
            for action in actions
        )

    wrapped._delivered_report_vitality = True
    module._meaningful = wrapped
    _INSTALLED = True


__all__ = ["install_downtime_vitality"]
