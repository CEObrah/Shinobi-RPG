from shinobi_runtime.sim import (
    CampaignDate,
    CampaignTime,
    CatchUpEngine,
    CounterRNG,
    EventConflict,
    EventOutcome,
    EventQueue,
    HostState,
    PlayerInterrupt,
    ScheduledEvent,
    SettlementError,
    boundaries_through,
    next_due,
)
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry
from shinobi_runtime.sim.catchup import (
    MAX_PLAYER_INTERRUPT_CONTEXT_CONTAINER_ITEMS,
    MAX_PLAYER_INTERRUPT_CONTEXT_DEPTH,
    MAX_PLAYER_INTERRUPT_CONTEXT_UTF8_BYTES,
    MAX_PLAYER_INTERRUPT_REASON_UTF8_BYTES,
    MAX_PUBLIC_FACT_CONTAINER_ITEMS,
    MAX_PUBLIC_FACT_DEPTH,
    MAX_PUBLIC_FACT_UTF8_BYTES,
)
from shinobi_runtime.sim.events import (
    MAX_EVENT_HOST_UTF8_BYTES,
    MAX_EVENT_ID_UTF8_BYTES,
    MAX_EVENT_KIND_UTF8_BYTES,
    MAX_EVENT_PAYLOAD_CONTAINER_ITEMS,
    MAX_EVENT_PAYLOAD_DEPTH,
    MAX_EVENT_PAYLOAD_UTF8_BYTES,
    MAX_EVENT_REF_UTF8_BYTES,
    MAX_EVENT_VISIBILITY_UTF8_BYTES,
)
import json
from pathlib import Path

import pytest


def time(value: str) -> CampaignTime:
    return CampaignTime.parse(value)


def event(event_id: str, due: str, priority: int = 10, **changes) -> ScheduledEvent:
    values = {
        "due_at": time(due),
        "priority": priority,
        "event_id": event_id,
        "kind": "test",
        "source_host": "host.source",
        "target_host": "host.target",
        "payload": {},
    }
    values.update(changes)
    return ScheduledEvent.build(**values)


def test_campaign_time_is_canonical_and_calendar_aware():
    current = time("SE-0061-02-28T23:59:59")
    assert str(current.add_seconds(1)) == "SE-0061-03-01T00:00:00"
    assert str(current.next_month_start(7)) == "SE-0061-03-01T07:00:00"


@pytest.mark.parametrize(
    "canonical,year",
    (
        ("SE--012-09-19", -12),
        ("SE-0000-11-04", 0),
        ("SE-0061-02-06", 61),
    ),
)
def test_campaign_date_round_trips_signed_zero_and_positive_years(canonical, year):
    parsed = CampaignDate.parse(canonical)
    assert parsed.year == year
    assert str(parsed) == canonical


@pytest.mark.parametrize(
    "canonical",
    (
        "SE--001-02-29",
        "SE-0001-02-29",
        "SE-0061-04-31",
        "SE-0061-13-01",
        "SE-0061-00-01",
        "SE-0061-01-00",
        "SE--000-01-01",
        "SE-00000-01-01",
    ),
)
def test_campaign_date_rejects_invalid_proleptic_dates(canonical):
    with pytest.raises(ValueError):
        CampaignDate.parse(canonical)


def test_campaign_date_uses_deterministic_leap_years_outside_datetime_range():
    assert str(CampaignDate.parse("SE--004-02-29")) == "SE--004-02-29"
    assert str(CampaignDate.parse("SE-0000-02-29")) == "SE-0000-02-29"
    assert str(CampaignDate.parse("SE-12000-02-29")) == "SE-12000-02-29"


@pytest.mark.parametrize(
    "canonical",
    (
        "SE--001-01-01T00:00:00",
        "SE-0000-01-01T00:00:00",
    ),
)
def test_campaign_time_remains_positive_only(canonical):
    with pytest.raises(ValueError):
        CampaignTime.parse(canonical)


def test_counter_rng_replays_and_separates_streams():
    first = CounterRNG(world_seed="world", transaction_id="tx.1", stream="combat")
    replay = CounterRNG(world_seed="world", transaction_id="tx.1", stream="combat")
    other = CounterRNG(world_seed="world", transaction_id="tx.1", stream="training")

    values = [first.draw_u64() for _ in range(4)]
    assert values == [replay.draw_u64() for _ in range(4)]
    assert values != [other.draw_u64() for _ in range(4)]
    assert [receipt.draw_index for receipt in first.receipts] == [0, 1, 2, 3]
    assert all(receipt.algorithm == "sha256_counter_u64" for receipt in first.receipts)


def test_randbelow_is_deterministic_and_receipted():
    first = CounterRNG(world_seed="world", transaction_id="tx.2", stream="selection")
    replay = CounterRNG(world_seed="world", transaction_id="tx.2", stream="selection")
    values = [first.randbelow(27) for _ in range(100)]
    assert values == [replay.randbelow(27) for _ in range(100)]
    assert all(0 <= value < 27 for value in values)
    assert len(first.receipts) >= 100


def test_queue_orders_by_due_priority_and_id():
    queue = EventQueue([
        event("event.c", "SE-0061-02-07T08:00:00", priority=20),
        event("event.b", "SE-0061-02-07T08:00:00", priority=10),
        event("event.a", "SE-0061-02-07T08:00:00", priority=10),
    ])
    target = time("SE-0061-02-07T08:00:00")
    assert [queue.pop_next_due(target).event_id for _ in range(3)] == [
        "event.a", "event.b", "event.c"
    ]


def test_queue_is_idempotent_and_rejects_conflicts():
    original = event("event.a", "SE-0061-02-07T08:00:00", dedupe_key="report")
    queue = EventQueue([original])
    assert queue.add(original) is False

    conflicting = event(
        "event.b",
        "SE-0061-02-07T09:00:00",
        dedupe_key="report",
    )
    try:
        queue.add(conflicting)
    except EventConflict:
        pass
    else:
        raise AssertionError("conflicting dedupe key was accepted")


@pytest.mark.parametrize(
    "changes",
    (
        {"event_id": "e" * (MAX_EVENT_ID_UTF8_BYTES + 1)},
        {"event_id": 7},
        {"kind": "k" * (MAX_EVENT_KIND_UTF8_BYTES + 1)},
        {"source_host": "s" * (MAX_EVENT_HOST_UTF8_BYTES + 1)},
        {"target_host": "t" * (MAX_EVENT_HOST_UTF8_BYTES + 1)},
        {"dedupe_key": "d" * (MAX_EVENT_REF_UTF8_BYTES + 1)},
        {"causation_id": "c" * (MAX_EVENT_REF_UTF8_BYTES + 1)},
        {"correlation_id": "c" * (MAX_EVENT_REF_UTF8_BYTES + 1)},
        {"visibility": "v" * (MAX_EVENT_VISIBILITY_UTF8_BYTES + 1)},
        {"priority": True},
        {"requires_player": 1},
    ),
)
def test_scheduled_event_rejects_unbounded_or_mistyped_text(changes):
    values = {
        "due_at": time("SE-0061-02-07T08:00:00"),
        "priority": 10,
        "event_id": "event.valid",
        "kind": "test",
        "source_host": "host.source",
        "target_host": "host.target",
        "payload": {},
    }
    values.update(changes)
    with pytest.raises((TypeError, ValueError)):
        ScheduledEvent.build(**values)


def test_scheduled_event_payload_has_utf8_node_depth_and_container_bounds():
    oversized_bytes = {
        "text": "界" * (MAX_EVENT_PAYLOAD_UTF8_BYTES // 3 + 1)
    }
    oversized_container = {
        "items": list(range(MAX_EVENT_PAYLOAD_CONTAINER_ITEMS + 1))
    }
    too_deep = "leaf"
    for _ in range(MAX_EVENT_PAYLOAD_DEPTH + 1):
        too_deep = [too_deep]
    oversized_nodes = 0
    for _ in range(10):
        oversized_nodes = [oversized_nodes, oversized_nodes]
    cyclic = []
    cyclic.append(cyclic)

    for payload in (
        oversized_bytes,
        oversized_container,
        {"nested": too_deep},
        {"tree": oversized_nodes},
        {"cycle": cyclic},
        [],
    ):
        with pytest.raises(ValueError):
            event(
                "event.payload-bounds",
                "SE-0061-02-07T08:00:00",
                payload=payload,
            )


def test_direct_event_constructor_rejects_oversized_raw_payload_before_parsing():
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        ScheduledEvent(
            due_at=time("SE-0061-02-07T08:00:00"),
            priority=10,
            event_id="event.raw-payload",
            kind="test",
            source_host="host.source",
            target_host="host.target",
            payload_json=" " * (MAX_EVENT_PAYLOAD_UTF8_BYTES + 1),
        )


def test_catchup_processes_successors_and_requires_safe_horizons():
    start = time("SE-0061-02-06T21:15:00")
    target = time("SE-0061-02-07T12:00:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=time("SE-0061-02-07T08:00:00"),
    )

    def handler(current, _host):
        if current.event_id == "event.first":
            successor = event("event.second", "SE-0061-02-07T10:00:00")
            return EventOutcome(
                emitted=(successor,),
                next_due=successor.due_at,
            )
        return EventOutcome(safe_through=target, authoritative_writes=("state/team/x.json",))

    result = CatchUpEngine({"test": handler}).settle(
        hosts={host.host_id: host},
        queue=EventQueue([event("event.first", "SE-0061-02-07T08:00:00")]),
        target=target,
    )
    assert result.processed_event_ids == ("event.first", "event.second")
    assert result.reached_time == target
    assert result.unsafe_host_ids == ()
    assert result.authoritative_writes == ("state/team/x.json",)


def test_player_interrupt_stops_at_exact_event_time():
    start = time("SE-0061-02-06T21:15:00")
    target = time("SE-0061-02-08T12:00:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=time("SE-0061-02-07T08:00:00"),
    )

    def handler(_event, _host):
        return EventOutcome(interrupt=PlayerInterrupt(reason="Wei must choose"))

    due = "SE-0061-02-07T08:00:00"
    result = CatchUpEngine({"test": handler}).settle(
        hosts={host.host_id: host},
        queue=EventQueue([event("event.choice", due)]),
        target=target,
    )
    assert str(result.reached_time) == due
    assert result.interrupt.reason == "Wei must choose"


def test_declared_player_event_interrupts_before_handler_or_queue_removal():
    start = time("SE-0061-02-06T21:15:00")
    target = time("SE-0061-02-08T12:00:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=time("SE-0061-02-07T08:00:00"),
    )
    called = []

    def handler(_event, _host):
        called.append(True)
        return EventOutcome(authoritative_writes=("state/illegal.json",))

    due = "SE-0061-02-07T08:00:00"
    queue = EventQueue([
        event("event.choice", due, requires_player=True),
    ])
    result = CatchUpEngine({"test": handler}).settle(
        hosts={host.host_id: host},
        queue=queue,
        target=target,
    )
    assert str(result.reached_time) == due
    assert result.processed_event_ids == ()
    assert called == []
    assert queue.peek().event_id == "event.choice"
    assert host.resolved_through == start


def test_interrupting_handler_cannot_write_or_emit_successors():
    start = time("SE-0061-02-06T21:15:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=time("SE-0061-02-07T08:00:00"),
    )

    def handler(_event, _host):
        return EventOutcome(
            authoritative_writes=("state/illegal.json",),
            interrupt=PlayerInterrupt(reason="Wei must choose"),
        )

    try:
        CatchUpEngine({"test": handler}).settle(
            hosts={host.host_id: host},
            queue=EventQueue([event("event.choice", "SE-0061-02-07T08:00:00")]),
            target=time("SE-0061-02-07T12:00:00"),
        )
    except SettlementError:
        pass
    else:
        raise AssertionError("interrupting handler wrote authoritative state")


def test_backdated_successor_fails_closed():
    start = time("SE-0061-02-06T21:15:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=time("SE-0061-02-07T08:00:00"),
    )

    def handler(_event, _host):
        return EventOutcome(emitted=(
            event("event.past", "SE-0061-02-07T07:59:59"),
        ))

    try:
        CatchUpEngine({"test": handler}).settle(
            hosts={host.host_id: host},
            queue=EventQueue([event("event.now", "SE-0061-02-07T08:00:00")]),
            target=time("SE-0061-02-07T12:00:00"),
        )
    except SettlementError:
        pass
    else:
        raise AssertionError("backdated successor was accepted")


def test_invalid_outcome_restores_event_and_does_not_advance_live_host():
    start = time("SE-0061-02-06T21:15:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=time("SE-0061-02-07T08:00:00"),
    )
    original = event("event.now", "SE-0061-02-07T08:00:00")
    queue = EventQueue([original])

    def handler(_event, detached_host):
        detached_host.advance_resolved(time("SE-0061-02-07T09:00:00"))
        return EventOutcome(next_due=time("SE-0061-02-07T08:00:00"))

    with pytest.raises(SettlementError, match="next_due does not match"):
        CatchUpEngine({"test": handler}).settle(
            hosts={host.host_id: host},
            queue=queue,
            target=time("SE-0061-02-07T12:00:00"),
        )
    assert host.resolved_through == start
    assert queue.snapshot() == (original,)


def test_unqueued_next_due_is_rejected_before_false_safe_closure():
    start = time("SE-0061-02-06T21:15:00")
    target = time("SE-0061-02-07T12:00:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=time("SE-0061-02-07T08:00:00"),
    )

    def handler(_event, _host):
        return EventOutcome(
            safe_through=target,
            next_due=time("SE-0061-02-07T10:00:00"),
        )

    original = event("event.now", "SE-0061-02-07T08:00:00")
    queue = EventQueue([original])
    with pytest.raises(SettlementError, match="next_due does not match"):
        CatchUpEngine({"test": handler}).settle(
            hosts={host.host_id: host},
            queue=queue,
            target=target,
        )
    assert host.resolved_through == start
    assert host.next_due == original.due_at
    assert queue.snapshot() == (original,)


def test_event_outcome_fanout_is_bounded_before_mutation():
    start = time("SE-0061-02-06T21:15:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=time("SE-0061-02-07T08:00:00"),
    )
    original = event("event.now", "SE-0061-02-07T08:00:00")
    queue = EventQueue([original])

    def handler(_event, _host):
        return EventOutcome(
            authoritative_writes=tuple(
                f"state/test/{index}.json" for index in range(65)
            )
        )

    with pytest.raises(SettlementError, match="write fanout"):
        CatchUpEngine({"test": handler}).settle(
            hosts={host.host_id: host},
            queue=queue,
            target=time("SE-0061-02-07T12:00:00"),
        )
    assert host.resolved_through == start
    assert queue.snapshot() == (original,)


def test_unknown_target_and_backdated_event_leave_queue_unchanged():
    start = time("SE-0061-02-07T09:00:00")
    unknown = event(
        "event.unknown",
        "SE-0061-02-07T10:00:00",
        target_host="host.missing",
    )
    unknown_queue = EventQueue([unknown])
    with pytest.raises(SettlementError, match="unknown host"):
        CatchUpEngine({"test": lambda _event, _host: EventOutcome()}).settle(
            hosts={},
            queue=unknown_queue,
            target=time("SE-0061-02-07T12:00:00"),
        )
    assert unknown_queue.snapshot() == (unknown,)

    backdated = event("event.backdated", "SE-0061-02-07T08:00:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
    )
    # Simulate a corrupt deserialized frontier that still claims this queued
    # timestamp.  Settlement must fail without consuming its only evidence.
    host.next_due = backdated.due_at
    backdated_queue = EventQueue([backdated])
    with pytest.raises(SettlementError, match="backdated event"):
        CatchUpEngine({"test": lambda _event, _host: EventOutcome()}).settle(
            hosts={host.host_id: host},
            queue=backdated_queue,
            target=time("SE-0061-02-07T12:00:00"),
        )
    assert host.resolved_through == start
    assert backdated_queue.snapshot() == (backdated,)


def test_aggregate_write_budget_stops_before_the_overflowing_event_mutates():
    start = time("SE-0061-02-06T21:15:00")
    first = event("event.first", "SE-0061-02-07T08:00:00")
    second = event("event.second", "SE-0061-02-07T09:00:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=first.due_at,
    )
    queue = EventQueue([first, second])

    def handler(current, _host):
        return EventOutcome(
            authoritative_writes=(f"state/test/{current.event_id}.json",),
            next_due=None if current.event_id == second.event_id else second.due_at,
        )

    with pytest.raises(SettlementError, match="aggregate write budget"):
        CatchUpEngine({"test": handler}).settle(
            hosts={host.host_id: host},
            queue=queue,
            target=second.due_at,
            authoritative_write_budget=1,
        )
    assert host.resolved_through == start
    assert host.next_due == first.due_at
    assert queue.snapshot() == (first, second)


def test_aggregate_successor_budget_bounds_multi_event_causal_work():
    start = time("SE-0061-02-06T21:15:00")
    first = event("event.first", "SE-0061-02-07T08:00:00")
    second = event("event.second", "SE-0061-02-07T09:00:00")
    first_successor = event("event.first-next", "SE-0061-02-07T10:00:00")
    second_successor = event("event.second-next", "SE-0061-02-07T11:00:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=first.due_at,
    )
    queue = EventQueue([first, second])

    def handler(current, _host):
        successor = (
            first_successor if current.event_id == first.event_id else second_successor
        )
        expected_next = second.due_at if current.event_id == first.event_id else first_successor.due_at
        return EventOutcome(emitted=(successor,), next_due=expected_next)

    with pytest.raises(SettlementError, match="aggregate successor budget"):
        CatchUpEngine({"test": handler}).settle(
            hosts={host.host_id: host},
            queue=queue,
            target=second.due_at,
            emitted_event_budget=1,
        )
    assert host.resolved_through == start
    assert host.next_due == first.due_at
    assert queue.snapshot() == (first, second)


def test_public_fact_structure_and_serialized_size_fail_before_live_mutation():
    oversized_bytes = {
        "text": "界" * (MAX_PUBLIC_FACT_UTF8_BYTES // 3 + 1)
    }
    oversized_container = {
        "items": list(range(MAX_PUBLIC_FACT_CONTAINER_ITEMS + 1))
    }
    too_deep = "leaf"
    for _ in range(MAX_PUBLIC_FACT_DEPTH + 1):
        too_deep = [too_deep]
    many_nodes = {
        "items": [
            {"a": index, "b": index, "c": index}
            for index in range(MAX_PUBLIC_FACT_CONTAINER_ITEMS)
        ]
    }

    for index, fact in enumerate(
        (
            oversized_bytes,
            oversized_container,
            {"nested": too_deep},
            many_nodes,
            {"binary": b"not-json"},
        )
    ):
        start = time("SE-0061-02-06T21:15:00")
        original = event(
            f"event.invalid-fact-{index}",
            "SE-0061-02-07T08:00:00",
        )
        host = HostState(
            host_id="host.target",
            kind="team",
            resolved_through=start,
            safe_through=start,
            handler_ref="test.1",
            rng_namespace="host.target",
            next_due=original.due_at,
        )
        queue = EventQueue([original])
        with pytest.raises(SettlementError, match="invalid public fact"):
            CatchUpEngine(
                {
                    "test": lambda _event, _host, fact=fact: EventOutcome(
                        public_facts=(fact,)
                    )
                }
            ).settle(
                hosts={host.host_id: host},
                queue=queue,
                target=time("SE-0061-02-07T12:00:00"),
            )
        assert host.resolved_through == start
        assert host.next_due == original.due_at
        assert queue.snapshot() == (original,)


def test_aggregate_public_fact_bytes_roll_back_the_whole_settlement():
    start = time("SE-0061-02-06T21:15:00")
    first = event("event.fact-first", "SE-0061-02-07T08:00:00")
    second = event("event.fact-second", "SE-0061-02-07T09:00:00")
    fact = {"detail": "bounded public fact"}
    serialized_size = len(
        json.dumps(
            fact,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=first.due_at,
    )
    queue = EventQueue([first, second])

    def handler(current, _host):
        return EventOutcome(
            public_facts=(fact,),
            next_due=second.due_at if current.event_id == first.event_id else None,
        )

    with pytest.raises(SettlementError, match="public-fact byte budget"):
        CatchUpEngine({"test": handler}).settle(
            hosts={host.host_id: host},
            queue=queue,
            target=second.due_at,
            public_fact_byte_budget=serialized_size * 2 - 1,
        )
    assert host.resolved_through == start
    assert host.next_due == first.due_at
    assert queue.snapshot() == (first, second)


def test_public_facts_and_player_interrupt_context_are_canonical_detached_copies():
    start = time("SE-0061-02-06T21:15:00")
    target = time("SE-0061-02-07T08:00:00")
    source_fact = {"z": 1, "nested": {"items": ["original"]}}
    context = {"z": 1, "nested": {"items": ["original"]}}
    player_interrupt = PlayerInterrupt(reason="Wei must choose", visible_context=context)
    context["nested"]["items"].append("mutated")
    assert player_interrupt.visible_context == {
        "nested": {"items": ["original"]},
        "z": 1,
    }

    original = event("event.detached-fact", str(target))
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=original.due_at,
    )
    result = CatchUpEngine(
        {
            "test": lambda _event, _host: EventOutcome(
                public_facts=(source_fact,),
                safe_through=target,
            )
        }
    ).settle(
        hosts={host.host_id: host},
        queue=EventQueue([original]),
        target=target,
    )
    source_fact["nested"]["items"].append("mutated")
    assert result.public_facts == (
        {"nested": {"items": ["original"]}, "z": 1},
    )


def test_player_interrupt_bounds_and_malformed_handler_interrupt_roll_back():
    with pytest.raises(ValueError, match="reason"):
        PlayerInterrupt(
            reason="界" * (MAX_PLAYER_INTERRUPT_REASON_UTF8_BYTES // 3 + 1)
        )
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        PlayerInterrupt(
            reason="bounded",
            visible_context={
                "text": "界" * (MAX_PLAYER_INTERRUPT_CONTEXT_UTF8_BYTES // 3 + 1)
            },
        )
    too_deep = "leaf"
    for _ in range(MAX_PLAYER_INTERRUPT_CONTEXT_DEPTH + 1):
        too_deep = [too_deep]
    with pytest.raises(ValueError, match="depth"):
        PlayerInterrupt(reason="bounded", visible_context={"nested": too_deep})
    with pytest.raises(ValueError, match="item limit"):
        PlayerInterrupt(
            reason="bounded",
            visible_context={
                "items": list(
                    range(MAX_PLAYER_INTERRUPT_CONTEXT_CONTAINER_ITEMS + 1)
                )
            },
        )

    malformed = object.__new__(PlayerInterrupt)
    object.__setattr__(malformed, "reason", "bounded")
    object.__setattr__(malformed, "visible_context", {"nested": too_deep})
    start = time("SE-0061-02-06T21:15:00")
    original = event("event.invalid-interrupt", "SE-0061-02-07T08:00:00")
    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=original.due_at,
    )
    queue = EventQueue([original])
    with pytest.raises(SettlementError, match="invalid player interrupt"):
        CatchUpEngine(
            {
                "test": lambda _event, _host: EventOutcome(
                    interrupt=malformed
                )
            }
        ).settle(
            hosts={host.host_id: host},
            queue=queue,
            target=time("SE-0061-02-07T12:00:00"),
        )
    assert host.resolved_through == start
    assert host.next_due == original.due_at
    assert queue.snapshot() == (original,)


def test_cross_host_successor_reconciles_next_due_and_safe_horizon():
    start = time("SE-0061-02-06T21:15:00")
    target = time("SE-0061-02-07T08:00:00")
    initial = event("event.first", "SE-0061-02-07T08:00:00", target_host="host.a")
    successor = event(
        "event.wake-b",
        "SE-0061-02-07T09:00:00",
        target_host="host.b",
    )
    host_a = HostState(
        host_id="host.a",
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="test.1",
        rng_namespace="host.a",
        next_due=initial.due_at,
    )
    host_b = HostState(
        host_id="host.b",
        kind="team",
        resolved_through=start,
        safe_through=time("SE-0061-02-08T00:00:00"),
        handler_ref="test.1",
        rng_namespace="host.b",
    )

    result = CatchUpEngine(
        {
            "test": lambda _event, _host: EventOutcome(
                emitted=(successor,),
                safe_through=target,
            )
        }
    ).settle(
        hosts={host_a.host_id: host_a, host_b.host_id: host_b},
        queue=EventQueue([initial]),
        target=target,
    )

    assert result.reached_time == target
    assert host_a.next_due is None
    assert host_b.next_due == successor.due_at
    assert host_b.safe_through == time("SE-0061-02-07T08:59:59")


def test_event_and_host_records_round_trip():
    original = event(
        "event.roundtrip",
        "SE-0061-02-07T08:00:00",
        payload={"z": 1, "a": ["x"]},
    )
    assert ScheduledEvent.from_record(original.to_record()) == original

    host = HostState(
        host_id="host.target",
        kind="team",
        resolved_through=time("SE-0061-02-06T21:15:00"),
        safe_through=time("SE-0061-02-07T00:00:00"),
        handler_ref="test.1",
        rng_namespace="host.target",
        next_due=time("SE-0061-02-07T08:00:00"),
    )
    assert HostState.from_record(host.to_record()) == host


def test_current_campaign_uses_persisted_causal_scheduler_without_legacy_frontier():
    root = Path(__file__).resolve().parents[2]
    record = json.loads((root / "state/time/causal-scheduler.json").read_text(encoding="utf-8"))
    scheduler = CausalSchedulerRegistry.from_record(record)

    assert scheduler.hosts
    assert len(scheduler.queue.snapshot()) == record["metrics"]["pending_event_count"]
    assert record["metrics"].get("global_person_scans", 0) == 0
    assert record["metrics"].get("global_faction_directory_scans", 0) == 0
    assert not (root / "state/time/frontier.json").exists()
    assert not (root / "state/time/coverage").exists()


def test_recurrence_enumerates_full_interval_not_only_first_boundary():
    first = time("SE-0061-02-12T07:00:00")
    target = time("SE-0061-03-05T07:00:00")
    recurrence = {"kind": "fixed_interval", "interval_seconds": 604800}
    assert tuple(str(value) for value in boundaries_through(first, recurrence, target)) == (
        "SE-0061-02-12T07:00:00",
        "SE-0061-02-19T07:00:00",
        "SE-0061-02-26T07:00:00",
        "SE-0061-03-05T07:00:00",
    )


def test_calendar_recurrence_crosses_year_without_year_zero():
    current = time("SE-0061-12-01T00:00:00")
    assert str(next_due(current, {"kind": "calendar_month_start"})) == (
        "SE-0062-01-01T00:00:00"
    )
    assert str(next_due(current, {"kind": "calendar_year_start", "clock": "07:00:00"})) == (
        "SE-0062-01-01T07:00:00"
    )


def test_periodic_scheduler_proves_safe_horizon_to_successor_for_sequential_advances():
    from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry, SchedulerHost, recurring_event, settle_scheduler

    start = time("SE-0061-02-11T00:00:00")
    first_due = time("SE-0061-02-11T07:00:00")
    first_target = time("SE-0061-02-11T12:00:00")
    second_target = time("SE-0061-02-15T12:00:00")
    successor_due = time("SE-0061-02-18T07:00:00")
    host_id = "host.test.periodic"
    recurrence = {"kind": "fixed_interval", "interval_seconds": 604800}
    event = recurring_event(
        kind="team.periodic_review",
        identity="test.periodic",
        host_id=host_id,
        due_at=first_due,
        recurrence=recurrence,
        payload={},
    )
    host = HostState(
        host_id=host_id,
        kind="team",
        resolved_through=start,
        safe_through=start,
        handler_ref="causal.scheduler",
        rng_namespace=host_id,
        next_due=first_due,
    )
    registry = CausalSchedulerRegistry(
        world_time=start,
        hosts={host_id: SchedulerHost(host, "team", None, {})},
        queue=EventQueue([event]),
        seeded_at=start,
        bootstrap_source="test",
        metrics={},
    )

    first = settle_scheduler(registry, target=first_target)
    assert first.reached_time == first_target
    assert host.next_due == successor_due
    assert host.safe_through == successor_due.add_seconds(-1)

    second = settle_scheduler(registry, target=second_target)
    assert second.reached_time == second_target
    assert second.processed_event_ids == ()
    assert second.unsafe_host_ids == ()
    assert host.next_due == successor_due
    assert host.safe_through == successor_due.add_seconds(-1)
