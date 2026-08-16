"""Sanitize unexpected time-settlement planner TypeError/ValueError failures."""
from __future__ import annotations

import re
import traceback
from functools import wraps
from typing import Any

from shinobi_runtime.api.contracts import CommandRejectedError

_INSTALLED = False


def _runtime_error_stage(exc: BaseException) -> str:
    selected = "unknown"
    for frame in traceback.extract_tb(exc.__traceback__):
        filename = frame.filename.replace("\\", "/")
        marker = "/shinobi_runtime/"
        if marker not in filename:
            continue
        relative = filename.split(marker, 1)[1]
        parts = relative.split("/")
        leaf = parts[-1].removesuffix(".py")
        parent = parts[-2] if len(parts) >= 2 else "runtime"
        if parent in {"commands", "api", "sim", "store", "tx"}:
            token = leaf
        else:
            token = f"{parent}_{leaf}"
        selected = re.sub(r"[^a-z0-9_]+", "_", token.lower()).strip("_") or "unknown"
    return selected


def install_time_planner_error_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    planner = module.CampaignCommandPlanner
    original = planner._advance_time
    if getattr(original, "_time_planner_error_guard", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, *args: Any, **kwargs: Any):
        try:
            return original(self, *args, **kwargs)
        except CommandRejectedError:
            raise
        except (TypeError, ValueError) as exc:
            stage = _runtime_error_stage(exc)
            kind = "type_error" if isinstance(exc, TypeError) else "value_error"
            raise CommandRejectedError(
                f"advance_time_internal__{stage}__{kind}"
            ) from exc

    wrapped._time_planner_error_guard = True  # type: ignore[attr-defined]
    planner._advance_time = wrapped
    _INSTALLED = True


__all__ = ["install_time_planner_error_guard", "_runtime_error_stage"]
