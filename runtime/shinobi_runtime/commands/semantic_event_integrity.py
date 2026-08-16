"""Deterministic semantic-event multiplicity and staged-archive integrity.

A single semantic command may lawfully emit more than one event of the same
kind while settling several internal causal boundaries. The legacy event ID
used only ``command digest + kind`` and therefore rejected the second distinct
same-kind event with ``semantic_event_id_conflict``. This extension preserves
the legacy ID for the first event, makes exact replays idempotent, and adds a
deterministic payload-derived suffix only when a command emits another distinct
event of the same kind.

Live planning treats the current hot registry plus pending archive after-images
as one staged semantic-history frontier. New event generation never scans cold
committed archives: duplicate detection for one command is transaction-local.
Explicit historical lookup may still consult committed archives and fails closed
when a persisted archive that could contain the requested event is invalid.
Pending archive paths created by the current transaction are never mistaken for
missing persisted files, including when a nested time plan is composed into a
later semantic command.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False
_PENDING_ARCHIVES = "__pending_archive_writes__"


def _refs(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if isinstance(value, str) and value})


def _event_without_id(event: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in event.items() if key != "id"}


def _event_suffix(event: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _event_without_id(event),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _pending_archives(registry: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    pending = registry.get(_PENDING_ARCHIVES, {})
    if not isinstance(pending, Mapping):
        raise CommandRejectedError("world_event_pending_archive_invalid")
    normalized: Dict[str, Mapping[str, Any]] = {}
    for path, archive in pending.items():
        if not isinstance(path, str) or not path or not isinstance(archive, Mapping):
            raise CommandRejectedError("world_event_pending_archive_invalid")
        if not isinstance(archive.get("events"), list):
            raise CommandRejectedError("world_event_pending_archive_invalid")
        normalized[path] = archive
    return normalized


def _event_in_rows(rows: object, event_id: str) -> Optional[Mapping[str, Any]]:
    if not isinstance(rows, list):
        return None
    for event in rows:
        if isinstance(event, Mapping) and event.get("id") == event_id:
            return event
    return None


def _staged_event_by_id(
    self: Any,
    registry: Mapping[str, Any],
    event_id: str,
) -> Optional[Mapping[str, Any]]:
    """Resolve only the current transaction's semantic-history frontier.

    Command digests make event IDs deterministic. Exact duplicate command
    requests are intercepted by receipt/idempotency handling before planning, so
    generation-time collision checks need only the hot after-image and archives
    already staged by this same command. Consulting committed cold history here
    creates no additional safety and can make a valid staged archive look
    missing before its atomic commit.
    """

    events = registry.get("events")
    if not isinstance(events, list):
        raise CommandRejectedError("world_event_registry_invalid")
    event = _event_in_rows(events, event_id)
    if event is not None:
        return event

    for archive in _pending_archives(registry).values():
        event = _event_in_rows(archive.get("events"), event_id)
        if event is not None:
            return event
    return None


def _world_event_by_id_staging_safe(
    self: Any,
    event_id: str,
    *,
    registry: Optional[Mapping[str, Any]] = None,
) -> Optional[Mapping[str, Any]]:
    """Read one event without treating pending archive paths as persisted files."""

    current = registry if registry is not None else self._world_events()
    if not isinstance(current, Mapping):
        raise CommandRejectedError("world_event_registry_invalid")
    events = current.get("events")
    if not isinstance(events, list):
        raise CommandRejectedError("world_event_registry_invalid")
    event = _event_in_rows(events, event_id)
    if event is not None:
        return event

    pending = _pending_archives(current)
    for archive in pending.values():
        event = _event_in_rows(archive.get("events"), event_id)
        if event is not None:
            return event

    refs = current.get("archive_refs")
    if not isinstance(refs, list):
        raise CommandRejectedError("world_event_registry_invalid")
    pending_paths = frozenset(pending)
    for path in reversed(refs):
        if not isinstance(path, str):
            raise CommandRejectedError("world_event_registry_invalid")
        if path in pending_paths:
            continue
        try:
            archive = self.repository.read_json(path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("world_event_archive_invalid") from exc
        archived_events = archive.get("events") if isinstance(archive, Mapping) else None
        if not isinstance(archived_events, list):
            raise CommandRejectedError("world_event_archive_invalid")
        event = _event_in_rows(archived_events, event_id)
        if event is not None:
            return event
    return None


def _world_event_record_and_digest_staging_safe(
    self: Any,
    event_id: str,
    *,
    registry: Optional[Mapping[str, Any]] = None,
) -> Tuple[Optional[Mapping[str, Any]], Optional[str]]:
    """Return an event and persisted digest when one actually exists.

    Pending archive after-images have no committed digest yet, so an event found
    there returns ``None`` for the digest. Persisted archive corruption remains a
    hard failure when exact historical lookup must traverse that archive.
    """

    current = registry if registry is not None else self._world_events()
    if not isinstance(current, Mapping):
        raise CommandRejectedError("world_event_registry_invalid")
    events = current.get("events")
    if not isinstance(events, list):
        raise CommandRejectedError("world_event_registry_invalid")
    event = _event_in_rows(events, event_id)
    if event is not None:
        return event, self.repository.digest(WORLD_EVENT_REGISTRY_PATH)

    pending = _pending_archives(current)
    for archive in pending.values():
        event = _event_in_rows(archive.get("events"), event_id)
        if event is not None:
            return event, None

    refs = current.get("archive_refs")
    if not isinstance(refs, list):
        raise CommandRejectedError("world_event_registry_invalid")
    pending_paths = frozenset(pending)
    for path in reversed(refs):
        if not isinstance(path, str):
            raise CommandRejectedError("world_event_registry_invalid")
        if path in pending_paths:
            continue
        try:
            archive = self.repository.read_json(path)
            digest = self.repository.digest(path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("world_event_archive_invalid") from exc
        archived_events = archive.get("events") if isinstance(archive, Mapping) else None
        if not isinstance(archived_events, list) or digest is None:
            raise CommandRejectedError("world_event_archive_invalid")
        event = _event_in_rows(archived_events, event_id)
        if event is not None:
            return event, digest
    return None, None


def _rehydrate_pending_archives_from_plan(
    registry: Mapping[str, Any],
    base: object,
) -> Dict[str, Any]:
    """Restore archive after-images stripped from a nested plan's hot registry.

    ``_world_event_writes`` deliberately removes the private pending map before
    persisting the hot registry and emits each archive as its own write. A
    composed semantic command that starts from that nested plan must put those
    archive after-images back into its in-memory registry before appending more
    events, otherwise the new archive refs look like missing committed files.
    """

    record = copy.deepcopy(dict(registry))
    writes = getattr(base, "writes", None)
    if not isinstance(writes, Mapping):
        return record
    refs = record.get("archive_refs")
    if not isinstance(refs, list):
        raise CommandRejectedError("world_event_registry_invalid")

    merged: Dict[str, Mapping[str, Any]] = dict(_pending_archives(record))
    for path in refs:
        if not isinstance(path, str):
            raise CommandRejectedError("world_event_registry_invalid")
        raw = writes.get(path)
        if raw is None:
            continue
        try:
            archive = json.loads(raw.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise CommandRejectedError("world_event_pending_archive_invalid") from exc
        if not isinstance(archive, Mapping) or not isinstance(archive.get("events"), list):
            raise CommandRejectedError("world_event_pending_archive_invalid")
        existing = merged.get(path)
        if existing is not None and dict(existing) != dict(archive):
            raise CommandRejectedError("world_event_pending_archive_conflict")
        merged[path] = dict(archive)

    if merged:
        record[_PENDING_ARCHIVES] = merged
    return record


def _append_semantic_event(
    self: Any,
    registry: Dict[str, Any],
    *,
    command: CommandEnvelope,
    kind: str,
    at: CampaignTime,
    host_refs: Iterable[str] = (),
    actor_refs: Iterable[str] = (),
    place_refs: Iterable[str] = (),
    causal_refs: Iterable[str] = (),
    affected_owner_refs: Iterable[str] = (),
    material_consequence_refs: Iterable[str] = (),
    classification: str = "restricted",
    audience_refs: Iterable[str] = (),
    knowledge_refs: Iterable[str] = (),
    route_refs: Iterable[str] = (),
    source_refs: Iterable[str] = (),
    reducer_ref: Optional[str] = None,
) -> str:
    events = registry.get("events")
    if not isinstance(events, list):
        raise CommandRejectedError("world_event_registry_invalid")

    actors = _refs(actor_refs)
    sources = _refs(source_refs) or actors or [command.actor_id]
    clean = re.sub(r"[^a-z0-9._-]+", "_", kind.lower()).strip("._-")
    base_id = f"event.{clean}.{command.digest[:20]}"
    event: Dict[str, Any] = {
        "id": base_id,
        "kind": kind,
        "status": "resolved",
        "timing": {
            "scheduled_for": None,
            "occurred_at": str(at),
            "started_at": str(at),
            "ended_at": str(at),
        },
        "host_refs": _refs(host_refs),
        "actor_refs": actors,
        "place_refs": _refs(place_refs),
        "causal_refs": _refs(causal_refs),
        "affected_owner_refs": _refs(affected_owner_refs),
        "material_consequence_refs": _refs(material_consequence_refs),
        "visibility": {
            "classification": classification,
            "witness_refs": actors,
            "audience_refs": _refs(audience_refs),
            "knowledge_refs": _refs(knowledge_refs),
            "route_refs": _refs(route_refs),
        },
        "provenance": {
            "source_kind": "semantic_command",
            "source_refs": sources,
            "archetype_ref": None,
            "recorded_at": str(at),
        },
        "execution": {
            "reducer_ref": reducer_ref or f"shinobi_runtime.commands.{kind}",
            "transaction_ref": "tx.gameplay." + command.digest,
            "receipt_refs": ["receipt.gameplay." + command.digest],
        },
        "supersedes_ref": None,
        "superseded_by_ref": None,
    }

    existing = _staged_event_by_id(self, registry, base_id)
    if existing is not None:
        if _event_without_id(existing) == _event_without_id(event):
            return base_id
        event_id = f"{base_id}.{_event_suffix(event)}"
        event["id"] = event_id
        existing = _staged_event_by_id(self, registry, event_id)
        if existing is not None:
            if _event_without_id(existing) == _event_without_id(event):
                return event_id
            raise CommandRejectedError("semantic_event_id_conflict")
    else:
        event_id = base_id

    events.append(event)
    self._roll_world_events(registry, at=at)
    return event_id


def install_semantic_event_integrity() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import planner as planner_module

    planner = planner_module.RepositoryCommandPlanner
    original_world_events_after = planner._world_events_after

    def world_events_after_with_pending(self: Any, base: object = None) -> Dict[str, Any]:
        record = original_world_events_after(self, base)
        if base is None:
            return record
        return _rehydrate_pending_archives_from_plan(record, base)

    planner._append_semantic_event = _append_semantic_event
    planner._world_event_by_id = _world_event_by_id_staging_safe
    planner._world_event_record_and_digest = _world_event_record_and_digest_staging_safe
    planner._world_events_after = world_events_after_with_pending
    _INSTALLED = True


__all__ = [
    "install_semantic_event_integrity",
    "_append_semantic_event",
    "_rehydrate_pending_archives_from_plan",
    "_world_event_by_id_staging_safe",
    "_world_event_record_and_digest_staging_safe",
]
