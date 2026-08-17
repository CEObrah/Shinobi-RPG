"""Final production guards for institution-review extension integrity.

Several campaign extensions wrap ``AutonomyCommandsMixin._apply_institution_autonomy_review``.
The underlying time reducer may gain routing-only keyword arguments before every
older wrapper is updated. This module is installed last. It preserves the current
call surface, strips only the known routing-only owner hint when the wrapped
chain cannot accept it, reconciles the aggregate institution review event after
the full extension stack returns, and performs one final integrity pass at the
semantic-history serialization boundary.

The serialization pass is deliberately narrow. It may suppress only a resolved
``institution_autonomy_reviewed`` aggregate record that still has no material
consequence refs. It never fabricates refs and never changes another event kind.
Concrete sub-events remain untouched. This preserves fail-closed semantic-history
validation while preventing bookkeeping-only institution reviews from becoming a
global time-progress wall.

This module does not own institution policy, population math, training, military
lifecycle, scheduling, or semantic-event validation. Those remain with the normal
reducers and validators.
"""
from __future__ import annotations

import copy
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
_AGGREGATE_KIND = "institution_autonomy_reviewed"


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
        # without rewriting its return packet. Normalize the packet instead of
        # resurrecting an event that no longer exists.
        normalized = dict(repaired)
        normalized["event_id"] = None
        return normalized
    if event.get("kind") != _AGGREGATE_KIND:
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
    # Suppress only the aggregate event. Concrete sub-events remain intact.
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


def _is_empty_aggregate_review(event: Any) -> bool:
    if not isinstance(event, Mapping):
        return False
    if event.get("kind") != _AGGREGATE_KIND or event.get("status") != "resolved":
        return False
    refs = event.get("material_consequence_refs")
    return not (
        isinstance(refs, list)
        and any(isinstance(ref, str) and ref for ref in refs)
    )


def _strip_empty_aggregate_reviews(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Return a serialization-safe copy without fake/no-op aggregate reviews.

    ``_roll_world_events`` may already have moved a just-created review into one
    or more pending archive segments before an outer compatibility wrapper can
    find it. The final serializer sees both the hot owner and pending archives,
    making it the last coherent place to enforce that no materially empty
    aggregate review reaches schema/domain validation.
    """
    cleaned = copy.deepcopy(dict(registry))
    hot = cleaned.get("events")
    if not isinstance(hot, list):
        raise CommandRejectedError("world_event_registry_invalid")
    hot[:] = [event for event in hot if not _is_empty_aggregate_review(event)]

    pending = cleaned.get("__pending_archive_writes__", {})
    if not isinstance(pending, dict):
        raise CommandRejectedError("world_event_archive_invalid")
    archived_count = cleaned.get("archived_event_count")
    if isinstance(archived_count, bool) or not isinstance(archived_count, int) or archived_count < 0:
        raise CommandRejectedError("world_event_registry_invalid")
    archive_refs = cleaned.get("archive_refs")
    if not isinstance(archive_refs, list):
        raise CommandRejectedError("world_event_registry_invalid")

    removed_from_archives = 0
    empty_archives: list[str] = []
    for path, archive in list(pending.items()):
        if not isinstance(path, str) or not isinstance(archive, dict):
            raise CommandRejectedError("world_event_archive_invalid")
        rows = archive.get("events")
        if not isinstance(rows, list):
            raise CommandRejectedError("world_event_archive_invalid")
        before = len(rows)
        rows[:] = [event for event in rows if not _is_empty_aggregate_review(event)]
        removed = before - len(rows)
        if not removed:
            continue
        removed_from_archives += removed
        archive["event_count"] = len(rows)
        if not rows:
            empty_archives.append(path)

    for path in empty_archives:
        pending.pop(path, None)
        archive_refs[:] = [ref for ref in archive_refs if ref != path]

    if removed_from_archives:
        if removed_from_archives > archived_count:
            raise CommandRejectedError("world_event_registry_invalid")
        cleaned["archived_event_count"] = archived_count - removed_from_archives
    return cleaned


def _install_serialization_guard() -> None:
    # Import lazily to avoid planner import cycles while campaign extensions are
    # being discovered.
    from shinobi_runtime.commands.planner import RepositoryCommandPlanner

    original = RepositoryCommandPlanner._world_event_writes
    if getattr(original, "_institution_review_serialization_guard", False):
        return

    @wraps(original)
    def world_event_writes(registry: Mapping[str, Any]):
        return original(_strip_empty_aggregate_reviews(registry))

    world_event_writes._institution_review_serialization_guard = True  # type: ignore[attr-defined]
    RepositoryCommandPlanner._world_event_writes = staticmethod(world_event_writes)


def install_institution_review_runtime_guard() -> None:
    """Install final call-contract, event, and serialization guards once."""
    global _INSTALLED

    original = AutonomyCommandsMixin._apply_institution_autonomy_review
    if not getattr(original, "_institution_review_runtime_guard", False):
        @wraps(original)
        def wrapped(self: Any, **kwargs: Any):
            forwarded = dict(kwargs)
            # ``institution_owner_ref`` is a routing hint used by the world-registry
            # time reducer. Current institution reducers derive their material owner
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

    _install_serialization_guard()
    _INSTALLED = True


__all__ = [
    "install_institution_review_runtime_guard",
    "_reconcile_final_event",
    "_strip_empty_aggregate_reviews",
]
