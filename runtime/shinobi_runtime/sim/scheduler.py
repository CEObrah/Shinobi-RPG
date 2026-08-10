"""Persistent bounded causal scheduler for production campaign time.

The scheduler is deliberately an orchestration authority, not a duplicate game
state database.  It stores host cursors and explicit wake events.  Domain facts
remain in their own owners.  Routine facts that can be derived analytically
(such as age from birth date) do not receive periodic per-person events.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Tuple

from .catchup import CatchUpEngine, CatchUpResult, EventOutcome
from .events import CampaignTime, EventQueue, ScheduledEvent
from .hosts import HostState
from .recurrence import RecurrenceError, next_due


SCHEMA = "causal-scheduler-registry"
OWNER_ID = "runtime.causal_scheduler"
OWNER_TYPE = "causal_scheduler"
HANDLER_REF = "causal.scheduler"

# Closed scheduler event vocabulary.  A due non-player event must have a fact
# handler, while an interrupt-only event must explicitly require the player.
# This prevents orphan event kinds from entering persistent time state and
# failing months later during catch-up.
PERIODIC_FACT_KINDS = frozenset((
    "faction.periodic_review",
    "canon_pressure.periodic_review",
    "world_registry.periodic_review",
    "economy.periodic_review",
    "house.periodic_review",
    "population.periodic_review",
    "team.periodic_review",
    "person_continuity.periodic_review",
    "person.recovery.periodic_review",
))
ONE_SHOT_FACT_KINDS = frozenset(("commitment.due",))
PLAYER_INTERRUPT_KINDS = frozenset(("scene.player_boundary", "mission.boundary", "commitment.due"))


def _validate_scheduled_event_kind(event: ScheduledEvent) -> None:
    if event.requires_player:
        if event.kind not in PLAYER_INTERRUPT_KINDS:
            raise ValueError(f"unsupported player scheduler event kind: {event.kind}")
        return
    if event.kind not in PERIODIC_FACT_KINDS and event.kind not in ONE_SHOT_FACT_KINDS:
        raise ValueError(f"scheduler event kind has no settlement handler: {event.kind}")


def _event_id(kind: str, identity: str, due_at: CampaignTime) -> str:
    material = f"{kind}\x00{identity}\x00{due_at}".encode("utf-8")
    return "evt.causal." + hashlib.sha256(material).hexdigest()[:24]


def _first_after(
    due_at: CampaignTime,
    recurrence: Mapping[str, Any],
    target: CampaignTime,
) -> Tuple[Optional[CampaignTime], int, CampaignTime]:
    """Compact periodic boundaries through target into one deterministic review.

    Returns ``(successor, count, latest_due)``.  The caller processes one event
    even if a long skip crosses many identical fallback boundaries.
    """

    latest = due_at
    count = 1
    successor = next_due(due_at, recurrence)
    while successor is not None and successor <= target:
        latest = successor
        count += 1
        successor = next_due(successor, recurrence)
        if count > 100_000:
            raise RecurrenceError("causal recurrence compaction limit exceeded")
    return successor, count, latest


@dataclass(frozen=True)
class SchedulerHost:
    state: HostState
    authority_kind: str
    owner_ref: Optional[str]
    metadata: Mapping[str, Any]

    def to_record(self) -> Mapping[str, Any]:
        return {
            "state": self.state.to_record(),
            "authority_kind": self.authority_kind,
            "owner_ref": self.owner_ref,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "SchedulerHost":
        if not isinstance(record, Mapping):
            raise TypeError("scheduler host must be an object")
        allowed = {"state", "authority_kind", "owner_ref", "metadata"}
        unknown = set(record) - allowed
        if unknown:
            raise ValueError(f"unknown scheduler host fields: {sorted(unknown)}")
        authority_kind = record.get("authority_kind")
        owner_ref = record.get("owner_ref")
        metadata = record.get("metadata")
        if not isinstance(authority_kind, str) or not authority_kind:
            raise ValueError("scheduler authority_kind must be non-empty")
        if owner_ref is not None and (not isinstance(owner_ref, str) or not owner_ref):
            raise ValueError("scheduler owner_ref must be text or null")
        if not isinstance(metadata, Mapping):
            raise ValueError("scheduler metadata must be an object")
        return cls(
            state=HostState.from_record(record.get("state")),
            authority_kind=authority_kind,
            owner_ref=owner_ref,
            metadata=dict(metadata),
        )


@dataclass
class CausalSchedulerRegistry:
    world_time: CampaignTime
    hosts: Dict[str, SchedulerHost]
    queue: EventQueue
    seeded_at: CampaignTime
    bootstrap_source: str
    metrics: Dict[str, Any]

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "CausalSchedulerRegistry":
        if not isinstance(record, Mapping):
            raise TypeError("causal scheduler registry must be an object")
        allowed = {
            "schema",
            "owner_id",
            "owner_type",
            "authority",
            "world_time",
            "seeded_at",
            "bootstrap_source",
            "hosts",
            "events",
            "metrics",
        }
        unknown = set(record) - allowed
        if unknown:
            raise ValueError(f"unknown causal scheduler fields: {sorted(unknown)}")
        if (
            record.get("schema") != SCHEMA
            or record.get("owner_id") != OWNER_ID
            or record.get("owner_type") != OWNER_TYPE
            or record.get("authority") is not True
        ):
            raise ValueError("invalid causal scheduler identity")
        raw_hosts = record.get("hosts")
        raw_events = record.get("events")
        metrics = record.get("metrics")
        if not isinstance(raw_hosts, Mapping) or not isinstance(raw_events, list):
            raise ValueError("causal scheduler requires hosts and events")
        if not isinstance(metrics, Mapping):
            raise ValueError("causal scheduler metrics must be an object")
        hosts = {
            host_id: SchedulerHost.from_record(value)
            for host_id, value in raw_hosts.items()
        }
        if any(host_id != host.state.host_id for host_id, host in hosts.items()):
            raise ValueError("scheduler host key does not match host state")
        queue = EventQueue(ScheduledEvent.from_record(item) for item in raw_events)
        for event in queue.snapshot():
            _validate_scheduled_event_kind(event)
        queued_by_host: Dict[str, Optional[CampaignTime]] = {
            host_id: min(
                (
                    event.due_at
                    for event in queue.snapshot()
                    if event.target_host == host_id
                ),
                default=None,
            )
            for host_id in hosts
        }
        for host_id, host in hosts.items():
            if host.state.next_due != queued_by_host[host_id]:
                raise ValueError(
                    f"scheduler host {host_id} next_due does not match event queue"
                )
        return cls(
            world_time=CampaignTime.parse(record.get("world_time")),
            hosts=hosts,
            queue=queue,
            seeded_at=CampaignTime.parse(record.get("seeded_at")),
            bootstrap_source=record.get("bootstrap_source"),
            metrics=dict(metrics),
        )

    def to_record(self) -> Mapping[str, Any]:
        return {
            "schema": SCHEMA,
            "owner_id": OWNER_ID,
            "owner_type": OWNER_TYPE,
            "authority": True,
            "world_time": str(self.world_time),
            "seeded_at": str(self.seeded_at),
            "bootstrap_source": self.bootstrap_source,
            "hosts": {
                host_id: self.hosts[host_id].to_record()
                for host_id in sorted(self.hosts)
            },
            "events": list(self.queue.to_records()),
            "metrics": dict(self.metrics),
        }

    def add_host(self, host: SchedulerHost) -> None:
        if host.state.host_id in self.hosts:
            raise ValueError(f"duplicate causal host: {host.state.host_id}")
        self.hosts[host.state.host_id] = host

    def upsert_event(self, event: ScheduledEvent) -> bool:
        _validate_scheduled_event_kind(event)
        if event.target_host not in self.hosts:
            raise ValueError(f"event targets unknown causal host: {event.target_host}")
        added = self.queue.add(event)
        if added:
            state = self.hosts[event.target_host].state
            due = min(
                item.due_at
                for item in self.queue.snapshot()
                if item.target_host == event.target_host
            )
            state.next_due = due
            if state.safe_through >= due:
                state.safe_through = due.add_seconds(-1)
        return added


def recurring_event(
    *,
    kind: str,
    identity: str,
    host_id: str,
    due_at: CampaignTime,
    recurrence: Mapping[str, Any],
    payload: Mapping[str, Any],
    priority: int = 100,
    visibility: str = "hidden",
    requires_player: bool = False,
) -> ScheduledEvent:
    merged = dict(payload)
    merged["recurrence"] = dict(recurrence)
    merged["identity"] = identity
    return ScheduledEvent.build(
        due_at=due_at,
        priority=priority,
        event_id=_event_id(kind, identity, due_at),
        kind=kind,
        source_host=host_id,
        target_host=host_id,
        payload=merged,
        dedupe_key=f"{kind}:{identity}",
        visibility=visibility,
        requires_player=requires_player,
    )


def one_shot_event(
    *,
    kind: str,
    identity: str,
    source_host: str,
    target_host: str,
    due_at: CampaignTime,
    payload: Mapping[str, Any],
    priority: int = 100,
    visibility: str = "hidden",
    requires_player: bool = False,
) -> ScheduledEvent:
    return ScheduledEvent.build(
        due_at=due_at,
        priority=priority,
        event_id=_event_id(kind, identity, due_at),
        kind=kind,
        source_host=source_host,
        target_host=target_host,
        payload=dict(payload),
        dedupe_key=f"{kind}:{identity}",
        visibility=visibility,
        requires_player=requires_player,
    )



def _safe_before(due: CampaignTime, current: CampaignTime) -> CampaignTime:
    value = due.add_seconds(-1)
    return value if value >= current else current


def settle_scheduler(
    registry: CausalSchedulerRegistry,
    *,
    target: CampaignTime,
    event_budget: int = 10_000,
) -> CatchUpResult:
    """Settle explicit causal events through ``target``.

    Production scheduling is event/host based. Routine people, teams, forces,
    and institutions do not receive periodic polling events merely to prove
    that nothing happened. Periodic reviews exist only for macro authorities
    that intentionally own a recurring review cadence, such as faction plans
    and conditional canon fronts.
    """

    if target < registry.world_time:
        raise ValueError("scheduler target precedes world time")

    # Hosts with no pending event are safe to advance analytically. This is the
    # core cold-world property: absence of an outward wake proves no scheduler
    # work is required for the interval.
    for wrapper in registry.hosts.values():
        state = wrapper.state
        if state.next_due is None and state.safe_through < target:
            state.extend_safe_horizon(target)

    def periodic_handler(event: ScheduledEvent, host: HostState) -> EventOutcome:
        payload = event.payload
        recurrence = payload.get("recurrence")
        if not isinstance(recurrence, Mapping):
            raise ValueError("periodic causal event lacks recurrence")
        successor, compacted_count, latest_due = _first_after(
            event.due_at, recurrence, target
        )
        emitted = ()
        if successor is not None:
            emitted = (
                recurring_event(
                    kind=event.kind,
                    identity=payload.get("identity"),
                    host_id=event.target_host,
                    due_at=successor,
                    recurrence=recurrence,
                    payload={
                        key: value
                        for key, value in payload.items()
                        if key not in ("recurrence", "identity")
                    },
                    priority=event.priority,
                    visibility=event.visibility,
                    requires_player=event.requires_player,
                ),
            )
        safe = target if successor is None else successor.add_seconds(-1)
        return EventOutcome(
            emitted=emitted,
            public_facts=(
                {
                    "scheduler_event_kind": event.kind,
                    "event_id": event.event_id,
                    "target_host": event.target_host,
                    "identity": payload.get("identity"),
                    "due_at": str(event.due_at),
                    "latest_due": str(latest_due),
                    "compacted_boundaries": compacted_count,
                    "payload": {
                        key: value
                        for key, value in payload.items()
                        if key != "recurrence"
                    },
                },
            ),
            safe_through=safe,
            next_due=successor,
        )

    def one_shot_fact_handler(event: ScheduledEvent, host: HostState) -> EventOutcome:
        return EventOutcome(
            public_facts=(
                {
                    "scheduler_event_kind": event.kind,
                    "event_id": event.event_id,
                    "target_host": event.target_host,
                    "identity": event.payload.get("identity") or event.payload.get("commitment_id"),
                    "due_at": str(event.due_at),
                    "latest_due": str(event.due_at),
                    "compacted_boundaries": 1,
                    "payload": dict(event.payload),
                },
            ),
            safe_through=target,
            next_due=None,
        )

    handlers = {kind: periodic_handler for kind in PERIODIC_FACT_KINDS}
    handlers.update({kind: one_shot_fact_handler for kind in ONE_SHOT_FACT_KINDS})
    engine = CatchUpEngine(handlers)
    host_states: MutableMapping[str, HostState] = {
        host_id: wrapper.state for host_id, wrapper in registry.hosts.items()
    }
    result = engine.settle(
        hosts=host_states,
        queue=registry.queue,
        target=target,
        event_budget=event_budget,
    )
    registry.world_time = result.reached_time
    registry.metrics = {
        **registry.metrics,
        "last_requested_target": str(target),
        "last_reached_time": str(result.reached_time),
        "last_processed_event_count": len(result.processed_event_ids),
        "last_public_fact_count": len(result.public_facts),
        "last_unsafe_host_count": len(result.unsafe_host_ids),
        "pending_event_count": len(registry.queue),
        "host_count": len(registry.hosts),
        "global_person_scans": 0,
        "global_faction_directory_scans": 0,
    }
    return result


__all__ = [
    "CausalSchedulerRegistry",
    "SchedulerHost",
    "settle_scheduler",
    "recurring_event",
    "one_shot_event",
    "SCHEMA",
    "OWNER_ID",
    "PERIODIC_FACT_KINDS",
    "ONE_SHOT_FACT_KINDS",
    "PLAYER_INTERRUPT_KINDS",
]
