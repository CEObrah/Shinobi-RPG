"""Bounded deterministic settlement across causal hosts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .events import (
    CampaignTime,
    EventQueue,
    ScheduledEvent,
    _bounded_json_object,
    _bounded_text,
)
from .hosts import HostState


class SettlementError(RuntimeError):
    pass


MAX_PLAYER_INTERRUPT_REASON_UTF8_BYTES = 512
MAX_PLAYER_INTERRUPT_CONTEXT_UTF8_BYTES = 16 * 1024
MAX_PLAYER_INTERRUPT_CONTEXT_NODES = 512
MAX_PLAYER_INTERRUPT_CONTEXT_DEPTH = 8
MAX_PLAYER_INTERRUPT_CONTEXT_CONTAINER_ITEMS = 128


@dataclass(frozen=True)
class PlayerInterrupt:
    reason: str
    visible_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _bounded_text(
            self.reason,
            "player interrupt reason",
            MAX_PLAYER_INTERRUPT_REASON_UTF8_BYTES,
        )
        canonical_context = _bounded_json_object(
            self.visible_context,
            label="player interrupt visible context",
            max_utf8_bytes=MAX_PLAYER_INTERRUPT_CONTEXT_UTF8_BYTES,
            max_nodes=MAX_PLAYER_INTERRUPT_CONTEXT_NODES,
            max_depth=MAX_PLAYER_INTERRUPT_CONTEXT_DEPTH,
            max_container_items=MAX_PLAYER_INTERRUPT_CONTEXT_CONTAINER_ITEMS,
        )
        object.__setattr__(
            self,
            "visible_context",
            json.loads(canonical_context),
        )


@dataclass(frozen=True)
class EventOutcome:
    emitted: Tuple[ScheduledEvent, ...] = ()
    public_facts: Tuple[Mapping[str, Any], ...] = ()
    authoritative_writes: Tuple[str, ...] = ()
    safe_through: Optional[CampaignTime] = None
    next_due: Optional[CampaignTime] = None
    interrupt: Optional[PlayerInterrupt] = None


@dataclass(frozen=True)
class CatchUpResult:
    reached_time: CampaignTime
    processed_event_ids: Tuple[str, ...]
    public_facts: Tuple[Mapping[str, Any], ...]
    authoritative_writes: Tuple[str, ...]
    interrupt: Optional[PlayerInterrupt]
    budget_exhausted: bool
    unsafe_host_ids: Tuple[str, ...]


EventHandler = Callable[[ScheduledEvent, HostState], EventOutcome]


MAX_EMITTED_EVENTS_PER_OUTCOME = 256
MAX_PUBLIC_FACTS_PER_OUTCOME = 256
MAX_AUTHORITATIVE_WRITES_PER_OUTCOME = 64
MAX_TOTAL_EMITTED_EVENTS = 10_000
MAX_TOTAL_PUBLIC_FACTS = 4_096
MAX_TOTAL_AUTHORITATIVE_WRITES = 4_096
MAX_CAUSAL_HOSTS = 1_024
MAX_PENDING_EVENTS = 20_000
MAX_PUBLIC_FACT_UTF8_BYTES = 16 * 1024
MAX_PUBLIC_FACT_NODES = 512
MAX_PUBLIC_FACT_DEPTH = 8
MAX_PUBLIC_FACT_CONTAINER_ITEMS = 128
MAX_TOTAL_PUBLIC_FACT_UTF8_BYTES = 2 * 1024 * 1024


def _positive_budget(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _queued_next_due(
    events: Sequence[ScheduledEvent], host_id: str
) -> Optional[CampaignTime]:
    return min(
        (event.due_at for event in events if event.target_host == host_id),
        default=None,
    )


class CatchUpEngine:
    """Process the global frontier in canonical order.

    The engine does not declare an inactive host caught up merely because its
    queue is empty.  A host must possess a safe horizon through the requested
    target, preventing hidden overdue work from being skipped.
    """

    def __init__(self, handlers: Mapping[str, EventHandler]) -> None:
        self._handlers = dict(handlers)

    def settle(
        self,
        *,
        hosts: Mapping[str, HostState],
        queue: EventQueue,
        target: CampaignTime,
        event_budget: int = 10_000,
        emitted_event_budget: int = MAX_TOTAL_EMITTED_EVENTS,
        public_fact_budget: int = MAX_TOTAL_PUBLIC_FACTS,
        authoritative_write_budget: int = MAX_TOTAL_AUTHORITATIVE_WRITES,
        public_fact_byte_budget: int = MAX_TOTAL_PUBLIC_FACT_UTF8_BYTES,
        causal_host_budget: int = MAX_CAUSAL_HOSTS,
        pending_event_budget: int = MAX_PENDING_EVENTS,
    ) -> CatchUpResult:
        event_budget = _positive_budget(event_budget, "event_budget")
        emitted_event_budget = _positive_budget(
            emitted_event_budget, "emitted_event_budget"
        )
        public_fact_budget = _positive_budget(public_fact_budget, "public_fact_budget")
        authoritative_write_budget = _positive_budget(
            authoritative_write_budget, "authoritative_write_budget"
        )
        public_fact_byte_budget = _positive_budget(
            public_fact_byte_budget, "public_fact_byte_budget"
        )
        causal_host_budget = _positive_budget(causal_host_budget, "causal_host_budget")
        pending_event_budget = _positive_budget(
            pending_event_budget, "pending_event_budget"
        )
        if len(hosts) > causal_host_budget:
            raise SettlementError("causal host set exceeds its settlement budget")
        if any(key != host.host_id for key, host in hosts.items()):
            raise SettlementError("causal host map key does not match host identity")

        initial_events = queue.snapshot()
        if len(initial_events) > pending_event_budget:
            raise SettlementError("pending event queue exceeds its settlement budget")
        unknown_targets = sorted(
            {event.target_host for event in initial_events if event.target_host not in hosts}
        )
        if unknown_targets:
            raise SettlementError(
                "event targets unknown host: " + ",".join(unknown_targets)
            )
        for event in initial_events:
            if event.due_at <= hosts[event.target_host].resolved_through:
                raise SettlementError(
                    f"backdated event {event.event_id} does not follow host cursor"
                )
        for host in hosts.values():
            queued_due = _queued_next_due(initial_events, host.host_id)
            if host.next_due != queued_due:
                raise SettlementError(
                    f"host {host.host_id} next_due does not match its event queue"
                )

        # The complete settlement operates on detached authority images.  A
        # failure after several valid events therefore cannot expose a partially
        # advanced frontier to a caller that catches the exception.  Successful,
        # interrupted, unsafe, and budget-exhausted results are copied back only
        # after the result is internally coherent.
        live_hosts = hosts
        live_queue = queue
        hosts = {
            host_id: HostState.from_record(host.to_record())
            for host_id, host in live_hosts.items()
        }
        queue = EventQueue(initial_events)

        processed: List[str] = []
        public_facts: List[Mapping[str, Any]] = []
        writes: List[str] = []
        emitted_count = 0
        public_fact_count = 0
        public_fact_bytes = 0
        write_count = 0
        causal_host_ids = set(hosts)
        reached = min((host.resolved_through for host in hosts.values()), default=target)
        interrupt: Optional[PlayerInterrupt] = None

        while len(processed) < event_budget:
            pending = queue.peek()
            if (
                pending is not None
                and pending.due_at <= target
                and pending.requires_player
            ):
                interrupt = PlayerInterrupt(
                    reason=f"player decision required by {pending.event_id}",
                    visible_context={"event_id": pending.event_id},
                )
                reached = pending.due_at
                break
            event = queue.pop_next_due(target)
            if event is None:
                break
            try:
                host = hosts.get(event.target_host)
                if host is None:
                    raise SettlementError(
                        f"event targets unknown host: {event.target_host}"
                    )
                if event.due_at <= host.resolved_through:
                    raise SettlementError(
                        f"backdated event {event.event_id} does not follow host cursor"
                    )
                handler = self._handlers.get(event.kind)
                if handler is None:
                    raise SettlementError(
                        f"no handler registered for event kind: {event.kind}"
                    )

                # Handlers receive a detached image.  A malformed handler therefore
                # cannot mutate the live host before its complete outcome has been
                # checked and its event successors have been conflict-preflighted.
                handler_host = HostState.from_record(host.to_record())
                outcome = handler(event, handler_host)
                if not isinstance(outcome, EventOutcome):
                    raise SettlementError("event handler returned an invalid outcome")
                normalized_interrupt = None
                if outcome.interrupt is not None:
                    if not isinstance(outcome.interrupt, PlayerInterrupt):
                        raise SettlementError(
                            "event outcome contains an invalid player interrupt"
                        )
                    try:
                        normalized_interrupt = PlayerInterrupt(
                            reason=outcome.interrupt.reason,
                            visible_context=outcome.interrupt.visible_context,
                        )
                    except (TypeError, ValueError) as exc:
                        raise SettlementError(
                            "event outcome contains an invalid player interrupt"
                        ) from exc
                if len(outcome.emitted) > MAX_EMITTED_EVENTS_PER_OUTCOME:
                    raise SettlementError("event outcome exceeds successor fanout")
                if len(outcome.public_facts) > MAX_PUBLIC_FACTS_PER_OUTCOME:
                    raise SettlementError("event outcome exceeds public-fact fanout")
                if len(outcome.authoritative_writes) > MAX_AUTHORITATIVE_WRITES_PER_OUTCOME:
                    raise SettlementError("event outcome exceeds write fanout")
                normalized_public_facts = []
                outcome_public_fact_bytes = 0
                for item in outcome.public_facts:
                    try:
                        canonical_fact = _bounded_json_object(
                            item,
                            label="public fact",
                            max_utf8_bytes=MAX_PUBLIC_FACT_UTF8_BYTES,
                            max_nodes=MAX_PUBLIC_FACT_NODES,
                            max_depth=MAX_PUBLIC_FACT_DEPTH,
                            max_container_items=MAX_PUBLIC_FACT_CONTAINER_ITEMS,
                        )
                        normalized_fact = json.loads(canonical_fact)
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise SettlementError(
                            "event outcome contains an invalid public fact"
                        ) from exc
                    normalized_public_facts.append(normalized_fact)
                    outcome_public_fact_bytes += len(
                        canonical_fact.encode("utf-8", errors="strict")
                    )
                if any(
                    not isinstance(path, str) or not path
                    for path in outcome.authoritative_writes
                ):
                    raise SettlementError("event outcome contains an invalid write path")
                if len(outcome.authoritative_writes) != len(
                    set(outcome.authoritative_writes)
                ):
                    raise SettlementError("event outcome contains duplicate write paths")
                if emitted_count + len(outcome.emitted) > emitted_event_budget:
                    raise SettlementError("settlement exceeds aggregate successor budget")
                if public_fact_count + len(outcome.public_facts) > public_fact_budget:
                    raise SettlementError("settlement exceeds aggregate public-fact budget")
                if (
                    public_fact_bytes + outcome_public_fact_bytes
                    > public_fact_byte_budget
                ):
                    raise SettlementError(
                        "settlement exceeds aggregate public-fact byte budget"
                    )
                if write_count + len(outcome.authoritative_writes) > authoritative_write_budget:
                    raise SettlementError("settlement exceeds aggregate write budget")
                if normalized_interrupt is not None and (
                    outcome.emitted or outcome.authoritative_writes
                ):
                    raise SettlementError(
                        "an interrupting handler may not emit events or authoritative writes"
                    )
                if (
                    outcome.safe_through is not None
                    and outcome.safe_through < event.due_at
                ):
                    raise SettlementError("handler returned a safe horizon before its event")
                if (
                    outcome.safe_through is not None
                    and outcome.safe_through < host.safe_through
                ):
                    raise SettlementError("handler attempted to reduce its safe horizon")

                prospective_queue = EventQueue(queue.snapshot())
                affected_host_ids = {host.host_id}
                for emitted in outcome.emitted:
                    if not isinstance(emitted, ScheduledEvent):
                        raise SettlementError("event outcome contains an invalid successor")
                    if emitted.due_at < event.due_at:
                        raise SettlementError(
                            f"event {event.event_id} emitted backdated successor {emitted.event_id}"
                        )
                    target_host = hosts.get(emitted.target_host)
                    if target_host is None:
                        raise SettlementError(
                            f"successor targets unknown host: {emitted.target_host}"
                        )
                    if emitted.due_at <= target_host.resolved_through or (
                        emitted.target_host == host.host_id
                        and emitted.due_at <= event.due_at
                    ):
                        raise SettlementError(
                            f"event {event.event_id} emitted successor at or before "
                            f"target host cursor: {emitted.event_id}"
                        )
                    affected_host_ids.add(emitted.target_host)
                    prospective_queue.add(emitted)
                prospective_events = prospective_queue.snapshot()
                if len(prospective_events) > pending_event_budget:
                    raise SettlementError("settlement exceeds pending-event budget")
                prospective_causal_hosts = causal_host_ids | affected_host_ids
                if len(prospective_causal_hosts) > causal_host_budget:
                    raise SettlementError("settlement exceeds causal-host budget")

                next_due_by_host = {
                    host_id: _queued_next_due(prospective_events, host_id)
                    for host_id in affected_host_ids
                }
                expected_next_due = next_due_by_host[host.host_id]
                if outcome.next_due != expected_next_due:
                    raise SettlementError(
                        "handler next_due does not match the earliest queued successor"
                    )
                effective_safe = max(
                    host.safe_through,
                    event.due_at,
                    outcome.safe_through or event.due_at,
                )
                if expected_next_due is not None and effective_safe >= expected_next_due:
                    raise SettlementError(
                        "handler safe horizon reaches or passes its next_due"
                    )
            except BaseException:
                # Restore the event that was removed before handler dispatch.
                # No live host or successor queue mutation has happened yet.
                queue.add(event)
                raise

            host.advance_resolved(event.due_at)
            host.next_due = outcome.next_due
            if outcome.safe_through is not None:
                host.extend_safe_horizon(outcome.safe_through)

            for emitted in outcome.emitted:
                queue.add(emitted)
            for host_id, next_due_value in next_due_by_host.items():
                if host_id == host.host_id:
                    continue
                awakened = hosts[host_id]
                awakened.next_due = next_due_value
                if (
                    next_due_value is not None
                    and awakened.safe_through >= next_due_value
                ):
                    awakened.safe_through = next_due_value.add_seconds(-1)

            processed.append(event.event_id)
            public_facts.extend(normalized_public_facts)
            writes.extend(outcome.authoritative_writes)
            emitted_count += len(outcome.emitted)
            public_fact_count += len(outcome.public_facts)
            public_fact_bytes += outcome_public_fact_bytes
            write_count += len(outcome.authoritative_writes)
            causal_host_ids = prospective_causal_hosts
            reached = max(reached, event.due_at)

            if normalized_interrupt is not None:
                interrupt = normalized_interrupt
                reached = event.due_at
                break

        budget_exhausted = len(processed) >= event_budget and (
            queue.peek() is not None and queue.peek().due_at <= target
        )
        unsafe = tuple(sorted(
            host.host_id for host in hosts.values() if not host.is_safe_to(target)
        ))
        if interrupt is None and not budget_exhausted and not unsafe:
            reached = target

        for host_id, settled_host in hosts.items():
            live_host = live_hosts[host_id]
            live_host.resolved_through = settled_host.resolved_through
            live_host.safe_through = settled_host.safe_through
            live_host.next_due = settled_host.next_due
        live_queue.replace(queue.snapshot())

        return CatchUpResult(
            reached_time=reached,
            processed_event_ids=tuple(processed),
            public_facts=tuple(public_facts),
            authoritative_writes=tuple(dict.fromkeys(writes)),
            interrupt=interrupt,
            budget_exhausted=budget_exhausted,
            unsafe_host_ids=unsafe,
        )
