"""Sanitize unexpected autonomous-training planner exceptions.

Normal domain rejections keep their existing CommandRejectedError code. Only
unexpected TypeError/ValueError failures are translated into a bounded source-
stage token so live preview diagnostics can identify the failing composition
layer without exposing hidden campaign values or traceback contents.
"""
from __future__ import annotations

import traceback
from functools import wraps
from typing import Any

from shinobi_runtime.api.contracts import CommandRejectedError

_INSTALLED = False
_STAGE_SUFFIXES = (
    ("/joint_player_team_training.py", "joint_player_team_training"),
    ("/standing_training_participation.py", "standing_training_participation"),
    ("/living_world_training.py", "living_world_training"),
    ("/global_team_training_load.py", "global_team_training_load"),
)


def _training_error_stage(exc: BaseException) -> str:
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        filename = frame.filename.replace("\\", "/")
        for suffix, stage in _STAGE_SUFFIXES:
            if filename.endswith(suffix):
                return stage
    return "unknown"


def install_autonomous_training_error_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    planner = module.CampaignCommandPlanner
    original = planner._apply_autonomous_team_training
    if getattr(original, "_autonomous_training_error_guard", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, *args: Any, **kwargs: Any):
        try:
            return original(self, *args, **kwargs)
        except CommandRejectedError:
            raise
        except (TypeError, ValueError) as exc:
            stage = _training_error_stage(exc)
            kind = "type_error" if isinstance(exc, TypeError) else "value_error"
            raise CommandRejectedError(
                f"autonomous_team_training_internal__{stage}__{kind}"
            ) from exc

    wrapped._autonomous_training_error_guard = True  # type: ignore[attr-defined]
    planner._apply_autonomous_team_training = wrapped
    _INSTALLED = True


__all__ = ["install_autonomous_training_error_guard", "_training_error_stage"]
