"""Deterministic semantic-event multiplicity and replay integrity.

A single semantic command may lawfully emit more than one event of the same
kind while settling several internal causal boundaries. The legacy event ID
used only ``command digest + kind`` and therefore rejected the second distinct
same-kind event with ``semantic_event_id_conflict``. This extension preserves
the legacy ID for the first event, makes exact replays idempotent, and adds a
deterministic payload-derived suffix only when a command emits another distinct
event of the same kind. Pending archive writes are part of the same staged
registry and are searched before assigning an ID, so archive rolling cannot
reintroduce a duplicate base ID within one transaction.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime

_INSTALLED = False


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


def _staged_event_by_id(
    self: Any,
    registry: Mapping[str, Any],
    event_id: str,
) -> Optional[Mapping[str, Any]]:
    events = registry.get("events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, Mapping) and event.get("id") == event_id:
                return event

    pending = registry.get("__pending_archive_writes__")
    if isinstance(pending, Mapping):
        for archive in pending.values():
            archived = archive.get("events") if isinstance(archive, Mapping) else None
            if not isinstance(archived, list):
                continue
            for event in archived:
                if isinstance(event, Mapping) and event.get("id") == event_id:
                    return event

    return self._world_event_by_id(event_id, registry=registry)


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

    planner_module.RepositoryCommandPlanner._append_semantic_event = _append_semantic_event
    _INSTALLED = True


__all__ = ["install_semantic_event_integrity"]
