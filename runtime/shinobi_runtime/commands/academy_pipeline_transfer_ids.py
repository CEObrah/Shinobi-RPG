"""Compatibility repairs for Academy autonomy review integrity.

The base Academy pipeline currently formats ``suffix`` while constructing intake
and graduation transfer IDs, but the refactored reducer no longer binds that
name. The same reducer also emits one aggregate ``institution_autonomy_reviewed``
world event whose material consequence list only considers bundled operation
work. A monthly Academy, service-training, or military review can therefore make
real state changes while the aggregate event is left with an empty material
consequence list and fails world-event validation.

Production campaign extensions install this wrapper before other
institution-autonomy wrappers. It keeps the missing transfer suffix context-local,
then reconciles the aggregate review event against the reducer's own returned
sub-results. It never owns Academy population selection, graduation, force
accounting, or institutional policy; those remain in
``AutonomyCommandsMixin._apply_institution_autonomy_review``.

This module should be removed once the base reducer directly binds its Academy
transfer suffix and attributes/suppresses its aggregate review event correctly.
"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any, Mapping

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


def _append_material_ref(refs: list[str], value: Any) -> None:
    if isinstance(value, str) and value and value not in refs:
        refs.append(value)


def _derived_material_refs(result: Mapping[str, Any]) -> list[str]:
    """Project concrete material consequences already proven by reducer output."""
    refs: list[str] = []

    pipeline = result.get("population_pipeline")
    if isinstance(pipeline, Mapping):
        _append_material_ref(refs, pipeline.get("intake_transfer_id"))
        _append_material_ref(refs, pipeline.get("graduation_transfer_id"))

    service = result.get("service_training")
    if isinstance(service, Mapping):
        completed = service.get("completed")
        if isinstance(completed, int) and not isinstance(completed, bool) and completed > 0:
            force_ref = service.get("force_ref")
            if isinstance(force_ref, str) and force_ref:
                _append_material_ref(refs, f"service_training:{force_ref}:{completed}")
            else:
                _append_material_ref(refs, service.get("event_ref"))

    military = result.get("military_lifecycle")
    if isinstance(military, Mapping):
        for key in ("formation_ref", "force_ref", "operation_ref"):
            _append_material_ref(refs, military.get(key))
        recovered = military.get("medical_recovered")
        force_ref = military.get("force_ref")
        if (
            isinstance(recovered, int)
            and not isinstance(recovered, bool)
            and recovered > 0
            and isinstance(force_ref, str)
            and force_ref
        ):
            _append_material_ref(refs, f"medical_recovery:{force_ref}:{recovered}")

    return refs


def repair_institution_review_event(
    result: Mapping[str, Any],
    world_events: Any,
) -> Mapping[str, Any]:
    """Attribute or suppress the aggregate institution review event.

    The base reducer may already have valid operation consequence refs. Preserve
    those and add only consequences proven by its returned sub-results. If the
    review was genuinely material-free, remove the aggregate event instead of
    inventing a fake consequence merely to satisfy validation.
    """
    event_id = result.get("event_id")
    if not isinstance(event_id, str) or not event_id or not isinstance(world_events, dict):
        return result
    events = world_events.get("events")
    if not isinstance(events, list):
        return result

    event = next(
        (
            row
            for row in reversed(events)
            if isinstance(row, dict) and row.get("id") == event_id
        ),
        None,
    )
    if not isinstance(event, dict) or event.get("kind") != "institution_autonomy_reviewed":
        return result

    raw_refs = event.get("material_consequence_refs")
    refs = [
        ref
        for ref in raw_refs
        if isinstance(ref, str) and ref
    ] if isinstance(raw_refs, list) else []
    for ref in _derived_material_refs(result):
        _append_material_ref(refs, ref)

    if refs:
        event["material_consequence_refs"] = refs
        return result

    # A review with no material result is scheduler maintenance, not a material
    # world event. Removing only this just-appended aggregate event preserves any
    # detailed events emitted by concrete sub-reducers during the same review.
    events.remove(event)
    repaired = dict(result)
    repaired["event_id"] = None
    return repaired


def install_academy_pipeline_transfer_ids() -> None:
    """Install Academy transfer-id and aggregate review-event compatibility."""
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
            result = original(self, **kwargs)
            return repair_institution_review_event(result, kwargs.get("world_events"))
        finally:
            if token is not None:
                _SUFFIX.reset(token)

    wrapped._academy_pipeline_transfer_ids = True  # type: ignore[attr-defined]
    wrapped._institution_review_event_integrity = True  # type: ignore[attr-defined]
    AutonomyCommandsMixin._apply_institution_autonomy_review = wrapped
    _INSTALLED = True


__all__ = [
    "academy_pipeline_transfer_suffix",
    "install_academy_pipeline_transfer_ids",
    "repair_institution_review_event",
]
