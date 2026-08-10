"""Causal time settlement and bounded autonomous-world command support."""

from __future__ import annotations

import copy
import hashlib
import re
from typing import (
    Any,
    Dict,
    Iterable,
    Mapping,
    Optional,
)

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.sim.events import CampaignTime



class InternalEventCommandsMixin:
    def _append_internal_event(
        self,
        registry: Dict[str, Any],
        *,
        command: CommandEnvelope,
        identity: str,
        kind: str,
        at: CampaignTime,
        host_refs: Iterable[str] = (),
        actor_refs: Iterable[str] = (),
        causal_refs: Iterable[str] = (),
        affected_owner_refs: Iterable[str] = (),
        material_consequence_refs: Iterable[str] = (),
        classification: str = "restricted",
        audience_refs: Iterable[str] = (),
        knowledge_refs: Iterable[str] = (),
        source_refs: Iterable[str] = (),
        reducer_ref: Optional[str] = None,
    ) -> str:
        """Append one internal autonomous event without sharing a command-kind ID.

        One ``advance_time`` transaction may settle many autonomous hosts.  Those
        events still belong to the same atomic transaction, but each material
        consequence needs its own semantic identity.
        """

        events = registry.get("events")
        if not isinstance(events, list):
            raise CommandRejectedError("world_event_registry_invalid")

        def refs(values: Iterable[str]) -> list[str]:
            return sorted({value for value in values if isinstance(value, str) and value})

        digest = hashlib.sha256(
            f"{command.digest}\x00{identity}\x00{kind}\x00{at}".encode("utf-8")
        ).hexdigest()[:24]
        clean = re.sub(r"[^a-z0-9._-]+", "_", kind.lower()).strip("._-")
        event_id = f"event.{clean}.{digest}"
        if any(isinstance(item, Mapping) and item.get("id") == event_id for item in events):
            return event_id
        actors = refs(actor_refs)
        sources = refs(source_refs) or actors or [command.actor_id]
        events.append(
            {
                "id": event_id,
                "kind": kind,
                "status": "resolved",
                "timing": {
                    "scheduled_for": str(at),
                    "occurred_at": str(at),
                    "started_at": str(at),
                    "ended_at": str(at),
                },
                "host_refs": refs(host_refs),
                "actor_refs": actors,
                "place_refs": [],
                "causal_refs": refs(causal_refs),
                "affected_owner_refs": refs(affected_owner_refs),
                "material_consequence_refs": refs(material_consequence_refs),
                "visibility": {
                    "classification": classification,
                    "witness_refs": actors,
                    "audience_refs": refs(audience_refs),
                    "knowledge_refs": refs(knowledge_refs),
                    "route_refs": [],
                },
                "provenance": {
                    "source_kind": "autonomous_host_review",
                    "source_refs": sources,
                    "archetype_ref": None,
                    "recorded_at": str(at),
                },
                "execution": {
                    "reducer_ref": reducer_ref or f"shinobi_runtime.autonomy.{kind}",
                    "transaction_ref": ("tx.autonomous." if command.mode == "autonomous" else "tx.gameplay.") + command.digest,
                    "receipt_refs": [("receipt.autonomous." if command.mode == "autonomous" else "receipt.gameplay.") + command.digest],
                },
                "supersedes_ref": None,
                "superseded_by_ref": None,
            }
        )
        self._roll_world_events(registry, at=at)
        return event_id
    @staticmethod
    def _roll_world_events(registry: Dict[str, Any], *, at: CampaignTime) -> None:
        """Seal full semantic-event segments so the hot owner stays bounded.

        Archive segments are append-once transaction writes.  The hot registry
        retains only the newest events plus compact archive references, keeping
        normal gameplay write cost independent of total campaign history.
        """

        events = registry.get("events")
        limit = registry.get("segment_limit")
        if not isinstance(events, list) or isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise CommandRejectedError("world_event_registry_invalid")
        while len(events) > limit:
            seq = registry.get("next_archive_seq")
            refs = registry.get("archive_refs")
            archived_count = registry.get("archived_event_count")
            if (
                isinstance(seq, bool) or not isinstance(seq, int) or seq < 1
                or not isinstance(refs, list)
                or isinstance(archived_count, bool) or not isinstance(archived_count, int) or archived_count < 0
            ):
                raise CommandRejectedError("world_event_registry_invalid")
            segment_events = copy.deepcopy(events[:limit])
            del events[:limit]
            path = f"state/history/events/segment-{seq:06d}.json"
            if path in refs:
                raise CommandRejectedError("world_event_archive_conflict")
            pending = registry.setdefault("__pending_archive_writes__", {})
            if not isinstance(pending, dict) or path in pending:
                raise CommandRejectedError("world_event_archive_conflict")
            pending[path] = {
                "schema": "world-event-archive",
                "owner_id": f"history.events.{seq:06d}",
                "owner_type": "world_event_archive",
                "segment_index": seq,
                "created_at": str(at),
                "event_count": len(segment_events),
                "events": segment_events,
            }
            refs.append(path)
            registry["archived_event_count"] = archived_count + len(segment_events)
            registry["next_archive_seq"] = seq + 1

