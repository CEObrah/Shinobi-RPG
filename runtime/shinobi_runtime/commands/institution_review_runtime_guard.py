"""Final production guard for institution-review extension integrity.

Several campaign extensions wrap ``AutonomyCommandsMixin._apply_institution_autonomy_review``.
The underlying time reducer may gain routing-only keyword arguments before every
older wrapper is updated.  This guard is installed last, preserves the current
call surface, strips only the known routing-only owner hint when the wrapped
chain cannot accept it, and then verifies/reconciles the aggregate institution
review event before the time plan is serialized.

It does not own institution policy, population math, training, military
lifecycle, or scheduling.  Those remain with the normal reducers.  The guard
exists solely to keep the production extension seam from turning one malformed
background review into a global time-progress wall.
"""
from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.academy_pipeline_transfer_ids import (
    _derived_material_refs,
    repair_institution_review_event,
)
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin

_INSTALLED = False


def _accepts_keyword(callable_obj: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False
    if name in parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _event_container(world_events: Any, event_id: str):
    if not isinstance(world_events, dict):
        return None, None
    events = world_events.get("events")
    if isinstance(events, list):
        for row in reversed(events):
            if isinstance(row, dict) and row.get("id") == event_id:
                return events, row
    pending = world_events.get("__pending_archive_writes__")
    if isinstance(pending, Mapping):
        for archive in pending.values():
            rows = archive.get("events") if isinstance(archive, dict) else None
            if not isinstance(rows, list):
                continue
            for row in reversed(rows):
                if isinstance(row, dict) and row.get("id") == event_id:
                    return rows, row
    return None, None


def _reconcile_final_event(result: Mapping[str, Any], world_events: Any) -> Mapping[str, Any]:
    repaired = repair_institution_review_event(result, world_events)
    event_id = repaired.get("event_id") if isinstance(repaired, Mapping) else None
    if not isinstance(event_id, str) or not event_id:
        return repaired

    container, event = _event_container(world_events, event_id)
    if not isinstance(event, dict):
        # A prior wrapper may have lawfully suppressed the no-op aggregate event
        # without rewriting its return packet.  Normalize the packet instead of
        # resurrecting an event that no longer exists.
        normalized = dict(repaired)
        normalized["event_id"] = None
        return normalized
    if event.get("kind") != "institution_autonomy_reviewed":
        raise CommandRejectedError("institution_autonomy_review_event_integrity_failed")

    raw_refs = event.get("material_consequence_refs")
    refs = [
        ref for ref in raw_refs
        if isinstance(ref, str) and ref
    ] if isinstance(raw_refs, list) else []
    for ref in _derived_material_refs(repaired):
        if ref not in refs:
            refs.append(ref)
    if refs:
        event["material_consequence_refs"] = refs
        return repaired

    # A materially empty review is scheduler maintenance, not semantic history.
    # Suppress only the aggregate event.  Concrete sub-events remain intact.
    if isinstance(container, list):
        container.remove(event)
        pending = world_events.get("__pending_archive_writes__") if isinstance(world_events, dict) else None
        if isinstance(pending, Mapping):
            for archive in pending.values():
                if not isinstance(archive, dict) or archive.get("events") is not container:
                    continue
                archive["event_count"] = len(container)
                archived_count = world_events.get("archived_event_count")
                if isinstance(archived_count, int) and not isinstance(archived_count, bool) and archived_count > 0:
                    world_events["archived_event_count"] = archived_count - 1
                break
    normalized = dict(repaired)
    normalized["event_id"] = None
    return normalized


def install_institution_review_runtime_guard() -> None:
    """Install the final call-contract and semantic-event guard once."""
    global _INSTALLED
    if _INSTALLED:
        return

    original = AutonomyCommandsMixin._apply_institution_autonomy_review
    if getattr(original, "_institution_review_runtime_guard", False):
        _INSTALLED = True
        return

    @wraps(original)
    def wrapped(self: Any, **kwargs: Any):
        forwarded = dict(kwargs)
        # ``institution_owner_ref`` is a routing hint used by the world-registry
        # time reducer.  Current institution reducers derive their material owner
        # from the supplied institution record and do not consume this hint.
        if (
            "institution_owner_ref" in forwarded
            and not _accepts_keyword(original, "institution_owner_ref")
        ):
            forwarded.pop("institution_owner_ref")
        result = original(self, **forwarded)
        if not isinstance(result, Mapping):
            raise CommandRejectedError("institution_autonomy_review_result_invalid")
        return _reconcile_final_event(result, kwargs.get("world_events"))

    wrapped._institution_review_runtime_guard = True  # type: ignore[attr-defined]
    AutonomyCommandsMixin._apply_institution_autonomy_review = wrapped
    _INSTALLED = True


__all__ = [
    "install_institution_review_runtime_guard",
    "_reconcile_final_event",
]
