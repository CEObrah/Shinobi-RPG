"""Sanitize unexpected time and event-seeking planner TypeError/ValueError failures."""
from __future__ import annotations

import re
import traceback
from functools import wraps
from typing import Any, Callable

from shinobi_runtime.api.contracts import CommandRejectedError

_INSTALLED = False


def _stage_from_filename(filename: str) -> str | None:
    normalized = filename.replace("\\", "/")
    marker = "/shinobi_runtime/"
    if marker not in normalized:
        return None
    relative = normalized.split(marker, 1)[1]
    parts = relative.split("/")
    leaf = parts[-1].removesuffix(".py")
    parent = parts[-2] if len(parts) >= 2 else "runtime"
    token = leaf if parent in {"commands", "api", "sim", "store", "tx"} else f"{parent}_{leaf}"
    return re.sub(r"[^a-z0-9_]+", "_", token.lower()).strip("_") or "unknown"


def _runtime_error_stage(exc: BaseException) -> str:
    selected = "unknown"
    for frame in traceback.extract_tb(exc.__traceback__):
        stage = _stage_from_filename(frame.filename)
        if stage is not None:
            selected = stage
    return selected


def _guard(method: Callable[..., Any], *, prefix: str, marker: str):
    if getattr(method, marker, False):
        return method

    @wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any):
        try:
            return method(self, *args, **kwargs)
        except CommandRejectedError:
            raise
        except (TypeError, ValueError) as exc:
            stage = _runtime_error_stage(exc)
            kind = "type_error" if isinstance(exc, TypeError) else "value_error"
            raise CommandRejectedError(f"{prefix}__{stage}__{kind}") from exc

    setattr(wrapped, marker, True)
    return wrapped


def install_time_planner_error_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    planner = module.CampaignCommandPlanner
    planner._advance_time = _guard(
        planner._advance_time,
        prefix="advance_time_internal",
        marker="_time_planner_error_guard",
    )
    event_method = getattr(planner, "_advance_until_event", None)
    if callable(event_method):
        planner._advance_until_event = _guard(
            event_method,
            prefix="advance_until_event_internal",
            marker="_event_seeking_error_guard",
        )
    _INSTALLED = True


__all__ = [
    "install_time_planner_error_guard",
    "_runtime_error_stage",
    "_stage_from_filename",
]
