"""Compatibility fix for deterministic Academy autonomy transfer identifiers.

The base Academy pipeline currently formats ``suffix`` while constructing intake
and graduation transfer IDs, but the refactored reducer no longer binds that
name. Production campaign extensions install this wrapper before other
institution-autonomy wrappers. A context-local proxy restores the intended
stable identifier without introducing process-global request races.

This module is deliberately narrow. It does not own Academy population logic,
selection, graduation, or force accounting; those remain in
``AutonomyCommandsMixin._apply_institution_autonomy_review``.
"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any

from shinobi_runtime.commands.domains import autonomy as autonomy_module
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin

_INSTALLED = False
_SUFFIX: ContextVar[str | None] = ContextVar("academy_pipeline_transfer_suffix", default=None)


class _SuffixProxy:
    def _value(self) -> str:
        value = _SUFFIX.get()
        if value is None:
            raise RuntimeError("academy_pipeline_transfer_suffix_unbound")
        return value

    def __str__(self) -> str:
        return self._value()

    def __format__(self, format_spec: str) -> str:
        return format(self._value(), format_spec)


def academy_pipeline_transfer_suffix(institution_id: str, at: Any) -> str:
    """Return the stable transfer-id suffix for one institution review boundary."""
    if not isinstance(institution_id, str) or not institution_id:
        raise ValueError("institution_id must be a non-empty string")
    return hashlib.sha256(
        f"{institution_id}\x00{at}\x00academy-pipeline-transfer".encode("utf-8")
    ).hexdigest()[:20]


def install_academy_pipeline_transfer_ids() -> None:
    """Bind the missing Academy transfer suffix through a context-local wrapper."""
    global _INSTALLED
    if _INSTALLED:
        return

    original = AutonomyCommandsMixin._apply_institution_autonomy_review
    if getattr(original, "_academy_pipeline_transfer_ids", False):
        _INSTALLED = True
        return

    # The refactored base reducer resolves the unqualified name ``suffix`` from
    # its defining module. Keep that compatibility name as a context-local
    # proxy rather than a mutable string shared across concurrent requests.
    autonomy_module.suffix = _SuffixProxy()

    @wraps(original)
    def wrapped(self: Any, **kwargs: Any):
        institution = kwargs.get("institution")
        at = kwargs.get("at")
        institution_id = institution.get("id") if isinstance(institution, dict) else None
        token: Token[str | None] | None = None
        if isinstance(institution_id, str) and institution_id:
            token = _SUFFIX.set(academy_pipeline_transfer_suffix(institution_id, at))
        try:
            return original(self, **kwargs)
        finally:
            if token is not None:
                _SUFFIX.reset(token)

    wrapped._academy_pipeline_transfer_ids = True  # type: ignore[attr-defined]
    AutonomyCommandsMixin._apply_institution_autonomy_review = wrapped
    _INSTALLED = True


__all__ = ["academy_pipeline_transfer_suffix", "install_academy_pipeline_transfer_ids"]
