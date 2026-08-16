"""Preserve bounded diagnostics for unexpected internal preview failures.

The MCP input boundary already validates the caller's request shape.  Once a
validated command reaches CampaignOperations.preview_command, an unexpected
TypeError/ValueError is an internal planner defect, not bad caller input.  This
campaign extension converts only those unexpected failures into a bounded
OperationError while preserving typed CommandRejectedError codes exactly.
"""
from __future__ import annotations

import re
import traceback
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.api.operations import OperationError

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


def _preview_internal_code(exc: BaseException) -> str:
    kind = "type_error" if isinstance(exc, TypeError) else "value_error"
    return f"command_preview_internal__{_runtime_error_stage(exc)}__{kind}"


def install_preview_error_diagnostics() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.api import campaign_stable_operations as stable_module

    cls = stable_module.RouteAwareCampaignOperations
    original = cls.preview_command
    if getattr(original, "_preview_error_diagnostics", False):
        _INSTALLED = True
        return

    @wraps(original)
    def preview_command(self: Any, command: Any) -> Mapping[str, Any]:
        try:
            return original(self, command)
        except OperationError:
            raise
        except CommandRejectedError as exc:
            raise OperationError(422, exc.code) from exc
        except (TypeError, ValueError) as exc:
            raise OperationError(503, _preview_internal_code(exc)) from exc

    preview_command._preview_error_diagnostics = True  # type: ignore[attr-defined]
    cls.preview_command = preview_command
    _INSTALLED = True


__all__ = [
    "_preview_internal_code",
    "_runtime_error_stage",
    "_stage_from_filename",
    "install_preview_error_diagnostics",
]
