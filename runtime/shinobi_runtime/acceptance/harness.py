"""Phase 0--9 deterministic runtime stress and acceptance harness.

All campaign-like owners in this module are synthetic.  Long sequences reduce
immutable in-memory snapshots, and the only filesystem materialization occurs
inside a caller-provided temporary directory.  The real campaign repository is
never opened or mutated.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.combat import (
    BattleKernel,
    CapabilityProfile,
    CombatContract,
    CombatIntent,
    CombatObjective,
    CombatTiming,
    Engagement,
    InformationState,
    Participant,
    PersonnelState,
    PositionState,
    ResourceCost,
    ResourcePool,
    SideTerrain,
    TerrainState,
    required_draw_count,
    resolve_combat,
)
from shinobi_runtime.people import core_from_registry
from shinobi_runtime.reducers import (
    InformationClaim,
    PopulationPool,
    PopulationTransfer,
    TrainingInputs,
    apply_transfer,
    deliver_claim,
    neutral_proportional_selection,
    settle_training,
)
from shinobi_runtime.sim import (
    CampaignTime,
    CatchUpEngine,
    CounterRNG,
    EventOutcome,
    EventQueue,
    HostState,
    ScheduledEvent,
    next_due,
)
from shinobi_runtime.store import content_root
from shinobi_runtime.tx.canonical import canonical_sha256, sha256_bytes


class AcceptanceFailure(AssertionError):
    """A deterministic runtime acceptance invariant was violated."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


@dataclass(frozen=True)
class OperationalBudgets:
    """Hard CI and effect bounds enforced by the acceptance harness."""

    mixed_campaign_turns: int = 20
    sequential_turn_reductions: int = 1000
    max_touched_owners_per_turn: int = 3
    max_touched_owners_per_event: int = 1
    long_horizon_event_budget: int = 1024
    stale_person_event_budget: int = 24
    inactive_host_event_budget: int = 128
    large_battle_participants: int = 16
    large_battle_engagements: int = 8
    large_battle_max_touched_owners: int = 32
    max_pending_events_after_closure: int = 4
    max_runtime_milliseconds: int = 30_000

    def __post_init__(self) -> None:
        for name, value in self.to_record().items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                if name == "max_pending_events_after_closure" and value == 0:
                    continue
                raise ValueError(f"operational budget {name} must be positive")
        if self.large_battle_engagements > self.large_battle_participants:
            raise ValueError("large battle engagements exceed participants")
        if self.large_battle_participants != 2 * self.large_battle_engagements:
            raise ValueError(
                "large battle fixture requires two participants per engagement"
            )

    def to_record(self) -> Dict[str, int]:
        return {
            "mixed_campaign_turns": self.mixed_campaign_turns,
            "sequential_turn_reductions": self.sequential_turn_reductions,
            "max_touched_owners_per_turn": self.max_touched_owners_per_turn,
            "max_touched_owners_per_event": self.max_touched_owners_per_event,
            "long_horizon_event_budget": self.long_horizon_event_budget,
            "stale_person_event_budget": self.stale_person_event_budget,
            "inactive_host_event_budget": self.inactive_host_event_budget,
            "large_battle_participants": self.large_battle_participants,
            "large_battle_engagements": self.large_battle_engagements,
            "large_battle_max_touched_owners": self.large_battle_max_touched_owners,
            "max_pending_events_after_closure": self.max_pending_events_after_closure,
            "max_runtime_milliseconds": self.max_runtime_milliseconds,
        }


@dataclass(frozen=True)
class PhaseResult:
    phase: int
    name: str
    status: str
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.phase <= 9:
            raise ValueError("acceptance phase must be in 0..9")
        if not self.name:
            raise ValueError("acceptance phase name must be non-empty")
        if self.status != "passed":
            raise ValueError("completed acceptance results must be passed")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    def to_record(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "name": self.name,
            "status": self.status,
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class AcceptanceSummary:
    budgets: OperationalBudgets
    phases: Tuple[PhaseResult, ...]
    elapsed_milliseconds: int
    final_root_sha256: str
    replay_root_sha256: str

    SCHEMA = "shinobi.runtime-acceptance-result"
    VERSION = 1

    def __post_init__(self) -> None:
        if tuple(result.phase for result in self.phases) != tuple(range(10)):
            raise ValueError("acceptance summary must contain ordered phases 0..9")
        if self.elapsed_milliseconds < 0:
            raise ValueError("elapsed_milliseconds must be non-negative")
        for value in (self.final_root_sha256, self.replay_root_sha256):
            if (
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("acceptance roots must be SHA-256 digests")

    @property
    def passed(self) -> bool:
        return all(result.status == "passed" for result in self.phases)

    def to_record(self) -> Dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "version": self.VERSION,
            "passed": self.passed,
            "elapsed_milliseconds": self.elapsed_milliseconds,
            "budgets": self.budgets.to_record(),
            "phases": [phase.to_record() for phase in self.phases],
            "final_root_sha256": self.final_root_sha256,
            "replay_root_sha256": self.replay_root_sha256,
        }


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


@dataclass(frozen=True)
class _OwnerImage:
    path: str
    content: bytes

    def __post_init__(self) -> None:
        if not self.path.startswith("state/") or self.path.startswith("state/../"):
            raise ValueError("synthetic acceptance owners must remain below state/")
        if not isinstance(self.content, bytes):
            raise TypeError("owner image content must be bytes")


@dataclass(frozen=True)
class _CampaignSnapshot:
    owners: Tuple[_OwnerImage, ...]

    def __post_init__(self) -> None:
        owners = tuple(sorted(self.owners, key=lambda owner: owner.path))
        if not owners or len(owners) != len({owner.path for owner in owners}):
            raise ValueError("synthetic snapshot owners must be non-empty and unique")
        object.__setattr__(self, "owners", owners)
        meta = self.payload("state/meta.json")
        revision = meta.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("synthetic meta revision must be non-negative")

    @property
    def revision(self) -> int:
        return self.payload("state/meta.json")["revision"]

    @property
    def root_sha256(self) -> str:
        entries = [
            {
                "path": owner.path,
                "sha256": sha256_bytes(owner.content),
                "size": len(owner.content),
            }
            for owner in self.owners
        ]
        return canonical_sha256(
            {"algorithm": "sha256-path-content-v1", "entries": entries}
        )

    def payload(self, path: str) -> Dict[str, Any]:
        for owner in self.owners:
            if owner.path == path:
                value = json.loads(owner.content.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("synthetic owner payload must be an object")
                return value
        raise KeyError(path)

    def replace_payloads(
        self, updates: Mapping[str, Mapping[str, Any]]
    ) -> Tuple["_CampaignSnapshot", Tuple[str, ...]]:
        current = {owner.path: owner.content for owner in self.owners}
        touched: List[str] = []
        for path, payload in updates.items():
            if path not in current:
                raise KeyError(path)
            proposed = _canonical_bytes(payload)
            if proposed != current[path]:
                current[path] = proposed
                touched.append(path)
        return (
            _CampaignSnapshot(
                tuple(_OwnerImage(path, content) for path, content in current.items())
            ),
            tuple(sorted(touched)),
        )


@dataclass(frozen=True)
class _TurnReduction:
    snapshot: _CampaignSnapshot
    touched_owner_refs: Tuple[str, ...]
    before_revision: int
    after_revision: int
    before_root_sha256: str
    after_root_sha256: str
    public_records: Tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class _TurnRun:
    final_snapshot: _CampaignSnapshot
    roots: Tuple[str, ...]
    max_touched_owners: int
    population_total: int
    resource_total: int
    public_record_count: int


def _initial_snapshot() -> _CampaignSnapshot:
    payloads: Dict[str, Mapping[str, Any]] = {
        "state/meta.json": {
            "schema": "synthetic-acceptance-meta",
            "campaign_id": "acceptance.synthetic",
            "revision": 0,
            "world_time": "SE-0061-01-01T00:00:00",
        },
        "state/actor.json": {
            "schema": "synthetic-actor",
            "skill": 40,
            "training_residual": "0.000",
        },
        "state/population.json": {
            "schema": "synthetic-population",
            "pools": {
                "house": {
                    "total": 20,
                    "dimensions": {"age": {"adult": 15, "minor": 5}},
                },
                "village": {
                    "total": 100,
                    "dimensions": {"age": {"adult": 70, "minor": 30}},
                },
            },
        },
        "state/resources.json": {
            "schema": "synthetic-resources",
            "accounts": {"field": 100, "reserve": 1000},
        },
        "state/information.json": {
            "schema": "synthetic-information",
            "deliveries": [],
        },
        "state/mission.json": {
            "schema": "synthetic-mission",
            "cycles_completed": 0,
            "progress_milli": 0,
        },
    }
    return _CampaignSnapshot(
        tuple(
            _OwnerImage(path=path, content=_canonical_bytes(payload))
            for path, payload in payloads.items()
        )
    )


def _population_total(snapshot: _CampaignSnapshot) -> int:
    pools = snapshot.payload("state/population.json")["pools"]
    return sum(pool["total"] for pool in pools.values())


def _resource_total(snapshot: _CampaignSnapshot) -> int:
    accounts = snapshot.payload("state/resources.json")["accounts"]
    return sum(accounts.values())


def _pool(pool_id: str, payload: Mapping[str, Any]) -> PopulationPool:
    return PopulationPool(
        pool_id=pool_id,
        total=payload["total"],
        dimensions=payload["dimensions"],
    )


def _pool_record(pool: PopulationPool) -> Dict[str, Any]:
    return {
        "total": pool.total,
        "dimensions": {
            dimension: dict(categories)
            for dimension, categories in pool.dimensions.items()
        },
    }


def _reduce_turn(snapshot: _CampaignSnapshot, turn_index: int) -> _TurnReduction:
    if turn_index < 0:
        raise ValueError("turn_index must be non-negative")
    before_root = snapshot.root_sha256
    before_revision = snapshot.revision
    meta = snapshot.payload("state/meta.json")
    current_time = CampaignTime.parse(meta["world_time"])
    meta["revision"] = before_revision + 1
    meta["world_time"] = str(current_time.add_seconds(3600))
    updates: Dict[str, Mapping[str, Any]] = {"state/meta.json": meta}
    public_records: Tuple[Mapping[str, Any], ...] = ()
    turn_kind = turn_index % 5

    if turn_kind == 0:
        actor = snapshot.payload("state/actor.json")
        outcome = settle_training(
            TrainingInputs(
                scheduled_hours=1,
                attendance=1,
                available_instructor_hours=1,
                required_instructor_hours=1,
                facility_slots=1,
                required_slots=1,
                equipment_sets=1,
                required_sets=1,
                instructor_quality_factor=1,
                facility_quality_factor=1,
                equipment_factor=1,
                health_factor=1,
                recovery_factor=1,
                relevance_factor=1,
                difficulty_fit_factor=1,
                aptitude=100,
                experience_modifier=1,
                current_value=actor["skill"],
                residual_units=actor["training_residual"],
                representation="exact",
            )
        )
        actor["skill"] = outcome.ending_value
        actor["training_residual"] = format(outcome.residual_units, "f")
        updates["state/actor.json"] = actor
    elif turn_kind == 1:
        owner = snapshot.payload("state/population.json")
        pools = owner["pools"]
        cycle = turn_index // 5
        source_id, destination_id = (
            ("village", "house") if cycle % 2 == 0 else ("house", "village")
        )
        source = _pool(source_id, pools[source_id])
        destination = _pool(destination_id, pools[destination_id])
        selected = neutral_proportional_selection(source, 1)
        source_after, destination_after = apply_transfer(
            source,
            destination,
            PopulationTransfer(
                transfer_id=f"transfer.{turn_index:04d}",
                source_pool_id=source_id,
                destination_pool_id=destination_id,
                count=1,
                selected_dimensions=selected,
                selection_mode="neutral_proportional",
            ),
        )
        pools[source_id] = _pool_record(source_after)
        pools[destination_id] = _pool_record(destination_after)
        updates["state/population.json"] = owner
    elif turn_kind == 2:
        owner = snapshot.payload("state/resources.json")
        accounts = owner["accounts"]
        cycle = turn_index // 5
        source_id, destination_id = (
            ("reserve", "field") if cycle % 2 == 0 else ("field", "reserve")
        )
        _require(accounts[source_id] > 0, "synthetic resource source was exhausted")
        accounts[source_id] -= 1
        accounts[destination_id] += 1
        updates["state/resources.json"] = owner
    elif turn_kind == 3:
        owner = snapshot.payload("state/information.json")
        collected_at = current_time
        claim = InformationClaim(
            claim_id=f"claim.{turn_index:04d}",
            subject_ref="subject:hostile-front",
            source_ref="source:synthetic-scout",
            collected_at=collected_at,
            epistemic_kind="observation",
            confidence_milli=900,
            fact_ref=f"secret:unpublished-order.{turn_index:04d}",
            evidence_refs=(f"evidence:scout-note.{turn_index:04d}",),
        )
        delivery = deliver_claim(
            claim,
            delivery_id=f"delivery.{turn_index:04d}",
            sender_ref="person:scout",
            recipient_ref="person:commander",
            channel="sealed_report",
            delivered_at=collected_at,
            channel_confidence_milli=800,
        )
        public_record = delivery.to_record()
        _require(
            "secret:" not in json.dumps(public_record, sort_keys=True),
            "information delivery leaked its authoritative fact reference",
        )
        owner["deliveries"].append(public_record)
        updates["state/information.json"] = owner
        public_records = (public_record,)
    else:
        mission = snapshot.payload("state/mission.json")
        progress = mission["progress_milli"] + 100
        if progress >= 1000:
            mission["cycles_completed"] += 1
            progress = 0
        mission["progress_milli"] = progress
        updates["state/mission.json"] = mission

    after, touched = snapshot.replace_payloads(updates)
    _require(after.revision == before_revision + 1, "gameplay revision did not advance by one")
    _require("state/meta.json" in touched, "gameplay turn did not touch campaign meta")
    return _TurnReduction(
        snapshot=after,
        touched_owner_refs=touched,
        before_revision=before_revision,
        after_revision=after.revision,
        before_root_sha256=before_root,
        after_root_sha256=after.root_sha256,
        public_records=public_records,
    )


def _run_turns(
    initial: _CampaignSnapshot,
    count: int,
    budgets: OperationalBudgets,
) -> _TurnRun:
    snapshot = initial
    roots = [snapshot.root_sha256]
    max_touched = 0
    population_total = _population_total(snapshot)
    resource_total = _resource_total(snapshot)
    public_record_count = 0
    for turn_index in range(count):
        reduction = _reduce_turn(snapshot, turn_index)
        _require(
            reduction.before_revision == turn_index,
            "sequential reducer observed an unexpected base revision",
        )
        _require(
            reduction.after_revision == turn_index + 1,
            "sequential gameplay revision step was not +1",
        )
        max_touched = max(max_touched, len(reduction.touched_owner_refs))
        _require(
            len(reduction.touched_owner_refs) <= budgets.max_touched_owners_per_turn,
            "turn exceeded its touched-owner budget",
        )
        snapshot = reduction.snapshot
        roots.append(reduction.after_root_sha256)
        public_record_count += len(reduction.public_records)
        _require(
            _population_total(snapshot) == population_total,
            "population was created or destroyed during pure turn reduction",
        )
        _require(
            _resource_total(snapshot) == resource_total,
            "resources were created or destroyed during pure turn reduction",
        )
    _require(
        b"secret:" not in next(
            owner.content
            for owner in snapshot.owners
            if owner.path == "state/information.json"
        ),
        "synthetic information owner retained an undisclosed fact reference",
    )
    return _TurnRun(
        final_snapshot=snapshot,
        roots=tuple(roots),
        max_touched_owners=max_touched,
        population_total=population_total,
        resource_total=resource_total,
        public_record_count=public_record_count,
    )


@dataclass(frozen=True)
class _HostSpec:
    host_id: str
    kind: str
    owner_ref: str
    recurrence: Mapping[str, Any]
    first_due: CampaignTime


@dataclass(frozen=True)
class _HostRun:
    event_count: int
    max_touched_owners: int
    pending_overdue: int
    pending_future: int
    public_fact_count: int
    authoritative_owner_count: int
    closure_hash: str
    hosts: Tuple[Mapping[str, Any], ...]


def _settle_hosts(
    *,
    start: CampaignTime,
    target: CampaignTime,
    specs: Sequence[_HostSpec],
    event_budget: int,
    budgets: OperationalBudgets,
) -> _HostRun:
    hosts = {
        spec.host_id: HostState(
            host_id=spec.host_id,
            kind=spec.kind,
            resolved_through=start,
            safe_through=start,
            handler_ref="acceptance",
            rng_namespace=spec.host_id,
            next_due=spec.first_due,
        )
        for spec in specs
    }
    events = EventQueue(
        ScheduledEvent.build(
            due_at=spec.first_due,
            priority=100,
            event_id=f"event.{spec.host_id}.000001",
            kind="acceptance.recurrence",
            source_host=spec.host_id,
            target_host=spec.host_id,
            payload={
                "sequence": 1,
                "owner_ref": spec.owner_ref,
                "recurrence": dict(spec.recurrence),
            },
            visibility="hidden",
        )
        for spec in specs
    )
    max_touched = 0

    def handler(event: ScheduledEvent, _host: HostState) -> EventOutcome:
        nonlocal max_touched
        payload = event.payload
        recurrence = payload["recurrence"]
        successor_due = next_due(event.due_at, recurrence)
        emitted: Tuple[ScheduledEvent, ...] = ()
        if successor_due is not None:
            sequence = payload["sequence"] + 1
            emitted = (
                ScheduledEvent.build(
                    due_at=successor_due,
                    priority=event.priority,
                    event_id=f"event.{event.target_host}.{sequence:06d}",
                    kind=event.kind,
                    source_host=event.source_host,
                    target_host=event.target_host,
                    payload={
                        "sequence": sequence,
                        "owner_ref": payload["owner_ref"],
                        "recurrence": recurrence,
                    },
                    visibility="hidden",
                ),
            )
        writes = (payload["owner_ref"],)
        max_touched = max(max_touched, len(writes))
        safe_through = (
            target
            if successor_due is None or successor_due > target
            else event.due_at
        )
        return EventOutcome(
            emitted=emitted,
            public_facts=(),
            authoritative_writes=writes,
            safe_through=safe_through,
            next_due=successor_due,
        )

    result = CatchUpEngine({"acceptance.recurrence": handler}).settle(
        hosts=hosts,
        queue=events,
        target=target,
        event_budget=event_budget,
    )
    overdue = sum(event.due_at <= target for event in events.snapshot()) + sum(
        host.next_due is not None and host.next_due <= target for host in hosts.values()
    )
    pending_future = len(events)
    _require(not result.budget_exhausted, "host settlement exhausted its event budget")
    _require(result.interrupt is None, "hidden host progression requested player input")
    _require(result.unsafe_host_ids == (), "host frontier failed safe-horizon closure")
    _require(result.reached_time == target, "host frontier did not reach target time")
    _require(overdue == 0, "host frontier retained overdue work")
    _require(
        len(events) <= budgets.max_pending_events_after_closure,
        "closed frontier retained unexpected pending events",
    )
    _require(
        max_touched <= budgets.max_touched_owners_per_event,
        "event exceeded its touched-owner budget",
    )
    _require(not result.public_facts, "hidden host settlement leaked public information")
    host_records = tuple(hosts[host_id].to_record() for host_id in sorted(hosts))
    closure_record = {
        "processed_event_ids": list(result.processed_event_ids),
        "hosts": list(host_records),
        "queue": list(events.to_records()),
    }
    return _HostRun(
        event_count=len(result.processed_event_ids),
        max_touched_owners=max_touched,
        pending_overdue=overdue,
        pending_future=pending_future,
        public_fact_count=len(result.public_facts),
        authoritative_owner_count=len(result.authoritative_writes),
        closure_hash=canonical_sha256(closure_record),
        hosts=host_records,
    )


def _long_horizon_specs(start: CampaignTime) -> Tuple[_HostSpec, ...]:
    return (
        _HostSpec(
            "host.economy",
            "inactive_region",
            "state/economy/synthetic.json",
            {"kind": "calendar_month_start", "clock": "00:00:00"},
            start.next_month_start(),
        ),
        _HostSpec(
            "host.institutions",
            "inactive_region",
            "state/institutions/synthetic.json",
            {"kind": "calendar_quarter_start", "clock": "00:00:00"},
            next_due(
                start,
                {"kind": "calendar_quarter_start", "clock": "00:00:00"},
            ),
        ),
        _HostSpec(
            "host.population",
            "inactive_region",
            "state/population/synthetic.json",
            {"kind": "calendar_year_start", "clock": "00:00:00"},
            next_due(
                start,
                {"kind": "calendar_year_start", "clock": "00:00:00"},
            ),
        ),
        _HostSpec(
            "host.front",
            "front",
            "state/front/synthetic.json",
            {"kind": "fixed_interval", "interval_seconds": 30 * 86_400},
            start.add_seconds(30 * 86_400),
        ),
    )


def _long_horizon_run(
    years: int, budgets: OperationalBudgets
) -> _HostRun:
    start = CampaignTime.parse("SE-0061-01-01T00:00:00")
    target = CampaignTime(start.year + years, 1, 1, 0, 0, 0)
    return _settle_hosts(
        start=start,
        target=target,
        specs=_long_horizon_specs(start),
        event_budget=budgets.long_horizon_event_budget,
        budgets=budgets,
    )


def _stale_person_run(budgets: OperationalBudgets) -> Tuple[_HostRun, str, str]:
    start = CampaignTime.parse("SE-0061-01-01T00:00:00")
    target = CampaignTime.parse("SE-0062-01-01T00:00:00")
    registry = {
        "schema": "person-core-registry",
        "id": "registry.acceptance",
        "owner_ref": "state/people/acceptance-registry.json",
        "people": {
            "person.stale": {
                "id": "person.stale",
                "name": "Stale Roster Person",
                "aliases": [],
                "birth_date": "SE-0040-01-01",
                "birth_date_source": "synthetic_fixture",
                "life_status": "alive",
                "role_profile_ref": "role.guard",
                "duty_tags": ["inactive_rotation"],
                "location_ref": "location.remote",
                "cohort_ref": "cohort.remote",
                "cohort_slot": 7,
                "component_refs": {},
                "resolved_through": str(start),
                "coverage_ref": "coverage.remote",
                "identity_cues": {"doctrine": "synthetic"},
            }
        },
    }
    core = core_from_registry(
        registry,
        person_id="person.stale",
        source_ref="state/people/acceptance-registry.json",
    )
    _require(core.representation == "rostered_cohort", "stale person was not rostered")
    _require(core.resolved_through == start, "stale person cursor was not at fixture start")
    spec = _HostSpec(
        host_id="host.person.stale",
        kind="rostered_person",
        owner_ref=core.source_ref,
        recurrence={"kind": "calendar_month_start", "clock": "00:00:00"},
        first_due=start.next_month_start(),
    )
    run = _settle_hosts(
        start=start,
        target=target,
        specs=(spec,),
        event_budget=budgets.stale_person_event_budget,
        budgets=budgets,
    )
    caught_up = replace(core, resolved_through=target)
    _require(caught_up.person_id == core.person_id, "catch-up changed stable person identity")
    _require(
        caught_up.representation == core.representation,
        "catch-up changed person representation",
    )
    return run, str(core.resolved_through), str(caught_up.resolved_through)


def _inactive_hosts_run(budgets: OperationalBudgets) -> _HostRun:
    start = CampaignTime.parse("SE-0061-01-01T00:00:00")
    target = CampaignTime.parse("SE-0062-01-01T00:00:00")
    specs = (
        _HostSpec(
            "host.region.remote",
            "inactive_region",
            "state/region/remote.json",
            {"kind": "calendar_month_start", "clock": "00:00:00"},
            start.next_month_start(),
        ),
        _HostSpec(
            "host.front.remote",
            "front",
            "state/front/remote.json",
            {"kind": "fixed_interval", "interval_seconds": 7 * 86_400},
            start.add_seconds(7 * 86_400),
        ),
    )
    return _settle_hosts(
        start=start,
        target=target,
        specs=specs,
        event_budget=budgets.inactive_host_event_budget,
        budgets=budgets,
    )


def _large_battle_run(budgets: OperationalBudgets) -> Dict[str, Any]:
    zero = CapabilityProfile(0, 0, 0, 0, 0, 0, 0, 0, 0)
    red_profile = CapabilityProfile(200, 100, 180, 120, 180, 50, 120, 100, 100)
    blue_profile = CapabilityProfile(40, 40, 40, 20, 40, 20, 20, 40, 40)
    objective_ref = "objective:red-eliminate"
    participants: List[Participant] = []
    engagements: List[Engagement] = []
    blue_refs = tuple(
        f"participant:blue.{index:02d}"
        for index in range(budgets.large_battle_engagements)
    )
    for index in range(budgets.large_battle_engagements):
        red_ref = f"participant:red.{index:02d}"
        blue_ref = blue_refs[index]
        participants.append(
            Participant(
                participant_ref=red_ref,
                authoritative_owner_ref=f"state/formation/red-{index:02d}.json",
                side_ref="side:red",
                sequence=index,
                representation="aggregate",
                capability=red_profile,
                kernel=BattleKernel(
                    source_ref=f"cache:red.{index:02d}",
                    source_sha256=canonical_sha256(
                        {"side": "red", "index": index, "profile": red_profile.to_record()}
                    ),
                    mean=red_profile,
                    spread=zero,
                ),
                personnel=PersonnelState(total=1000, active=1000),
                position=PositionState(zone_ref="zone:battlefield"),
                information=InformationState(observed_refs=(blue_ref,)),
                intent=CombatIntent(
                    action="attack",
                    objective_ref=objective_ref,
                    target_refs=(blue_ref,),
                    commitment_milli=1000,
                    lethal_force_milli=100,
                    resource_costs=(ResourceCost("resource:stamina", 5),),
                ),
                initiative=120,
                readiness=100,
                morale=120,
                cohesion=120,
                resources=(ResourcePool("resource:stamina", 100, 100),),
                effective_range_bands=(1,),
            )
        )
        participants.append(
            Participant(
                participant_ref=blue_ref,
                authoritative_owner_ref=f"state/formation/blue-{index:02d}.json",
                side_ref="side:blue",
                sequence=budgets.large_battle_engagements + index,
                representation="aggregate",
                capability=blue_profile,
                kernel=BattleKernel(
                    source_ref=f"cache:blue.{index:02d}",
                    source_sha256=canonical_sha256(
                        {"side": "blue", "index": index, "profile": blue_profile.to_record()}
                    ),
                    mean=blue_profile,
                    spread=zero,
                ),
                personnel=PersonnelState(total=1000, active=1000),
                position=PositionState(zone_ref="zone:battlefield"),
                information=InformationState(observed_refs=(red_ref,)),
                intent=CombatIntent(action="hold"),
                initiative=80,
                readiness=100,
                morale=100,
                cohesion=100,
                effective_range_bands=(1,),
            )
        )
        engagements.append(
            Engagement(
                engagement_ref=f"engagement:{index:02d}",
                actor_ref=red_ref,
                target_ref=blue_ref,
                range_band=1,
                line_of_sight=True,
            )
        )
    objective = CombatObjective(
        objective_ref=objective_ref,
        side_ref="side:red",
        kind="eliminate",
        target_refs=blue_refs,
        required_progress=1,
    )
    contract = CombatContract(
        combat_ref="combat:large-acceptance",
        transaction_ref="tx:large-acceptance",
        scale="battle",
        participants=tuple(participants),
        objectives=(objective,),
        engagements=tuple(engagements),
        terrain=TerrainState(
            terrain_ref="terrain:symmetric-field",
            side_modifiers=(
                SideTerrain("side:red"),
                SideTerrain("side:blue"),
            ),
        ),
        timing=CombatTiming(current_tick=0, exchange_seconds=6, max_ticks=2),
        rng_stream="combat:large-acceptance",
    )
    _require(
        len(contract.participants) == budgets.large_battle_participants,
        "large battle participant fixture diverged from its budget",
    )
    _require(
        len(contract.engagements) == budgets.large_battle_engagements,
        "large battle engagement fixture diverged from its budget",
    )
    rng = CounterRNG(
        world_seed="acceptance-world-seed",
        transaction_id=contract.transaction_ref,
        stream=contract.rng_stream,
    )
    for _ in range(required_draw_count(contract)):
        rng.draw_u64()
    plan = resolve_combat(contract, rng.receipts)
    _require(plan.resolution_mode == "kernel", "clear large battle did not use kernel fast path")
    _require(
        len(plan.participant_effects) <= budgets.large_battle_max_touched_owners,
        "large battle exceeded touched-owner budget",
    )
    _require(plan.objective_effects[0].achieved, "large battle objective did not resolve")
    before_people = sum(effect.before_personnel.total for effect in plan.participant_effects)
    after_people = sum(
        sum(effect.after_personnel.to_record()[field] for field in (
            "active", "wounded", "incapacitated", "killed", "captured", "escaped"
        ))
        for effect in plan.participant_effects
    )
    before_resources = sum(
        resource.current
        for effect in plan.participant_effects
        for resource in effect.before_resources
    )
    after_resources = sum(
        resource.current
        for effect in plan.participant_effects
        for resource in effect.after_resources
    )
    _require(before_people == after_people, "large battle created or lost population")
    _require(after_resources <= before_resources, "large battle created resources")
    replay = resolve_combat(contract, rng.receipts)
    _require(plan == replay, "large battle replay diverged")
    return {
        "participants": len(contract.participants),
        "engagements": len(contract.engagements),
        "resolution_mode": plan.resolution_mode,
        "touched_owners": len(plan.participant_effects),
        "personnel_before": before_people,
        "personnel_after": after_people,
        "resources_before": before_resources,
        "resources_after": after_resources,
        "objective_achieved": plan.objective_effects[0].achieved,
        "plan_sha256": canonical_sha256(plan.to_record()),
    }


def _git(root: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise AcceptanceFailure("Git is required for temporary snapshot acceptance")
    return subprocess.run(
        [executable, "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _materialize_snapshot(root: Path, snapshot: _CampaignSnapshot) -> None:
    for owner in snapshot.owners:
        path = root / owner.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(owner.content)


def _temp_git_acceptance(
    work_root: Path,
    initial: _CampaignSnapshot,
    final: _CampaignSnapshot,
) -> Dict[str, Any]:
    work_root.mkdir(parents=True, exist_ok=True)
    repository_root = Path(
        tempfile.mkdtemp(prefix="shinobi-acceptance-", dir=str(work_root))
    )
    _materialize_snapshot(repository_root, initial)
    _git(repository_root, "init", "-q")
    _git(repository_root, "config", "user.email", "acceptance@example.invalid")
    _git(repository_root, "config", "user.name", "Acceptance Harness")
    _git(repository_root, "add", "state")
    _git(repository_root, "commit", "-qm", "synthetic baseline")
    _materialize_snapshot(repository_root, final)
    _git(repository_root, "add", "state")
    _git(repository_root, "commit", "-qm", "1000 pure gameplay reductions")
    disk_root = content_root(repository_root, include_roots=("state",))
    _require(
        disk_root.root_sha256 == final.root_sha256,
        "temporary Git snapshot root differs from in-memory replay root",
    )
    _require(_git(repository_root, "status", "--porcelain") == "", "temporary Git repo is dirty")
    commit_count = int(_git(repository_root, "rev-list", "--count", "HEAD"))
    _require(commit_count == 2, "temporary Git acceptance expected two snapshot commits")
    return {
        "commit_count": commit_count,
        "file_count": len(disk_root.entries),
        "disk_root_sha256": disk_root.root_sha256,
        "repository_pristine": True,
    }


def _execute_acceptance(
    work_root: Path, budgets: OperationalBudgets
) -> Tuple[Tuple[PhaseResult, ...], str, str]:
    phases: List[PhaseResult] = []
    initial = _initial_snapshot()
    _require(initial.revision == 0, "synthetic baseline revision is not zero")
    _require(_population_total(initial) == 120, "synthetic baseline population is invalid")
    _require(_resource_total(initial) == 1100, "synthetic baseline resources are invalid")
    phases.append(
        PhaseResult(
            0,
            "contract_and_budget_preflight",
            "passed",
            {
                "baseline_revision": initial.revision,
                "baseline_population": _population_total(initial),
                "baseline_resources": _resource_total(initial),
                "baseline_root_sha256": initial.root_sha256,
            },
        )
    )

    mixed = _run_turns(initial, budgets.mixed_campaign_turns, budgets)
    _require(
        mixed.final_snapshot.revision == budgets.mixed_campaign_turns,
        "20-turn campaign revision is incorrect",
    )
    phases.append(
        PhaseResult(
            1,
            "mixed_campaign_20_turns",
            "passed",
            {
                "turns": budgets.mixed_campaign_turns,
                "final_revision": mixed.final_snapshot.revision,
                "revision_delta_per_turn": 1,
                "max_touched_owners": mixed.max_touched_owners,
                "population_total": mixed.population_total,
                "population_delta": 0,
                "resource_total": mixed.resource_total,
                "resource_delta": 0,
                "public_record_count": mixed.public_record_count,
                "information_leakage_detected": False,
            },
        )
    )

    horizon_runs: Dict[int, _HostRun] = {}
    for phase, years in ((2, 1), (3, 5), (4, 10)):
        host_run = _long_horizon_run(years, budgets)
        horizon_runs[years] = host_run
        phases.append(
            PhaseResult(
                phase,
                f"advance_{years}_year" + ("" if years == 1 else "s"),
                "passed",
                {
                    "years": years,
                    "processed_events": host_run.event_count,
                    "max_touched_owners_per_event": host_run.max_touched_owners,
                    "pending_overdue": host_run.pending_overdue,
                    "pending_future": host_run.pending_future,
                    "unsafe_hosts": 0,
                    "public_fact_count": host_run.public_fact_count,
                    "closure_sha256": host_run.closure_hash,
                },
            )
        )

    stale, stale_before, stale_after = _stale_person_run(budgets)
    phases.append(
        PhaseResult(
            5,
            "stale_rostered_person_catch_up",
            "passed",
            {
                "resolved_through_before": stale_before,
                "resolved_through_after": stale_after,
                "processed_events": stale.event_count,
                "pending_overdue": stale.pending_overdue,
                "pending_future": stale.pending_future,
                "touched_owner_count": stale.authoritative_owner_count,
            },
        )
    )

    inactive = _inactive_hosts_run(budgets)
    phases.append(
        PhaseResult(
            6,
            "inactive_region_and_front_progression",
            "passed",
            {
                "hosts": len(inactive.hosts),
                "processed_events": inactive.event_count,
                "pending_overdue": inactive.pending_overdue,
                "pending_future": inactive.pending_future,
                "public_fact_count": inactive.public_fact_count,
                "closure_sha256": inactive.closure_hash,
            },
        )
    )

    battle = _large_battle_run(budgets)
    phases.append(
        PhaseResult(7, "large_scaled_battle", "passed", battle)
    )

    stress = _run_turns(initial, budgets.sequential_turn_reductions, budgets)
    _require(
        stress.final_snapshot.revision == budgets.sequential_turn_reductions,
        "1000-turn stress revision is incorrect",
    )
    phases.append(
        PhaseResult(
            8,
            "thousand_sequential_turn_reductions",
            "passed",
            {
                "turns": budgets.sequential_turn_reductions,
                "final_revision": stress.final_snapshot.revision,
                "revision_delta_per_turn": 1,
                "root_count": len(stress.roots),
                "max_touched_owners": stress.max_touched_owners,
                "population_total": stress.population_total,
                "population_delta": 0,
                "resource_total": stress.resource_total,
                "resource_delta": 0,
                "information_leakage_detected": False,
                "final_root_sha256": stress.final_snapshot.root_sha256,
            },
        )
    )

    replay = _run_turns(initial, budgets.sequential_turn_reductions, budgets)
    _require(stress.roots == replay.roots, "turn-by-turn replay root sequence diverged")
    _require(
        stress.final_snapshot.owners == replay.final_snapshot.owners,
        "replay owner bytes diverged",
    )
    ten_year_replay = _long_horizon_run(10, budgets)
    _require(
        ten_year_replay.closure_hash == horizon_runs[10].closure_hash,
        "ten-year frontier replay diverged",
    )
    git_result = _temp_git_acceptance(
        work_root, initial, stress.final_snapshot
    )
    phases.append(
        PhaseResult(
            9,
            "replay_and_temp_git_root_acceptance",
            "passed",
            {
                "turn_root_sequence_equal": True,
                "owner_bytes_equal": True,
                "ten_year_frontier_equal": True,
                **git_result,
            },
        )
    )
    return (
        tuple(phases),
        stress.final_snapshot.root_sha256,
        replay.final_snapshot.root_sha256,
    )


def run_acceptance(
    work_root: Optional[object] = None,
    *,
    budgets: Optional[OperationalBudgets] = None,
) -> AcceptanceSummary:
    """Run all ten phases and return a machine-readable immutable summary.

    ``work_root`` must be disposable.  When omitted, the harness owns and
    removes a temporary directory automatically.
    """

    active_budgets = budgets or OperationalBudgets()
    started = time.monotonic()
    if work_root is None:
        with tempfile.TemporaryDirectory(prefix="shinobi-runtime-acceptance-") as temporary:
            phases, final_root, replay_root = _execute_acceptance(
                Path(temporary), active_budgets
            )
    else:
        phases, final_root, replay_root = _execute_acceptance(
            Path(work_root), active_budgets
        )
    elapsed = int((time.monotonic() - started) * 1000)
    _require(
        elapsed < active_budgets.max_runtime_milliseconds,
        "acceptance harness exceeded its CI runtime budget",
    )
    summary = AcceptanceSummary(
        budgets=active_budgets,
        phases=phases,
        elapsed_milliseconds=elapsed,
        final_root_sha256=final_root,
        replay_root_sha256=replay_root,
    )
    serialized = json.dumps(summary.to_record(), sort_keys=True)
    _require("secret:" not in serialized, "machine result leaked hidden information")
    return summary
