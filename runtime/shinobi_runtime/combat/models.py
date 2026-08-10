"""Typed, fact-free contracts for deterministic scaled combat.

The objects in this module deliberately contain no repository paths, prose
interpretation, or persistence hooks.  A caller supplies authoritative owner
references and complete mechanical inputs; the resolver returns a bounded
effect plan for a transaction coordinator to validate and apply elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from shinobi_runtime.sim.rng import DrawReceipt


SCALES = frozenset(("duel", "skirmish", "formation", "battle"))
REPRESENTATIONS = frozenset(("exact", "rostered_cohort", "aggregate"))
INTENT_ACTIONS = frozenset(
    ("attack", "capture", "escape", "extract", "disengage", "hold", "secure", "delay")
)
OBJECTIVE_KINDS = frozenset(
    ("capture", "escape", "extract", "eliminate", "hold", "secure", "delay", "disengage")
)


def _reference(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise ValueError(f"{field} must be a non-empty reference")
    return value


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} must be in {minimum}..{maximum}")
    return value


def _unique_references(values: Tuple[str, ...], field: str) -> Tuple[str, ...]:
    normalized = tuple(values)
    for value in normalized:
        _reference(value, field)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must be unique")
    return normalized


@dataclass(frozen=True)
class ResourcePool:
    resource_ref: str
    capacity: int
    current: int

    def __post_init__(self) -> None:
        _reference(self.resource_ref, "resource_ref")
        _integer(self.capacity, "resource capacity", 0, 1_000_000_000)
        _integer(self.current, "resource current", 0, self.capacity)

    def to_record(self) -> Dict[str, Any]:
        return {
            "resource_ref": self.resource_ref,
            "capacity": self.capacity,
            "current": self.current,
        }


@dataclass(frozen=True)
class ResourceCost:
    resource_ref: str
    amount: int

    def __post_init__(self) -> None:
        _reference(self.resource_ref, "resource cost ref")
        _integer(self.amount, "resource cost amount", 1, 1_000_000_000)

    def to_record(self) -> Dict[str, Any]:
        return {"resource_ref": self.resource_ref, "amount": self.amount}


@dataclass(frozen=True)
class PersonnelState:
    """A conserved partition of the people represented by one participant."""

    total: int
    active: int
    wounded: int = 0
    incapacitated: int = 0
    killed: int = 0
    captured: int = 0
    escaped: int = 0

    def __post_init__(self) -> None:
        values = (
            self.active,
            self.wounded,
            self.incapacitated,
            self.killed,
            self.captured,
            self.escaped,
        )
        _integer(self.total, "personnel total", 1, 1_000_000_000)
        for name, value in zip(
            ("active", "wounded", "incapacitated", "killed", "captured", "escaped"),
            values,
        ):
            _integer(value, f"personnel {name}", 0, self.total)
        if sum(values) != self.total:
            raise ValueError("personnel categories must sum exactly to total")

    def to_record(self) -> Dict[str, int]:
        return {
            "total": self.total,
            "active": self.active,
            "wounded": self.wounded,
            "incapacitated": self.incapacitated,
            "killed": self.killed,
            "captured": self.captured,
            "escaped": self.escaped,
        }


@dataclass(frozen=True)
class CapabilityProfile:
    """Independent combat axes; it is intentionally not a scalar power score."""

    offense: int
    defense: int
    control: int
    mobility: int
    perception: int
    stealth: int
    capture: int
    escape: int
    protection: int

    def __post_init__(self) -> None:
        for field in (
            "offense",
            "defense",
            "control",
            "mobility",
            "perception",
            "stealth",
            "capture",
            "escape",
            "protection",
        ):
            _integer(getattr(self, field), f"capability {field}", 0, 200)

    def to_record(self) -> Dict[str, int]:
        return {
            "offense": self.offense,
            "defense": self.defense,
            "control": self.control,
            "mobility": self.mobility,
            "perception": self.perception,
            "stealth": self.stealth,
            "capture": self.capture,
            "escape": self.escape,
            "protection": self.protection,
        }


@dataclass(frozen=True)
class BattleKernel:
    """Derived broad-phase cache.  It is never an authoritative owner."""

    source_ref: str
    source_sha256: str
    mean: CapabilityProfile
    spread: CapabilityProfile
    derived_cache: bool = True

    def __post_init__(self) -> None:
        _reference(self.source_ref, "kernel source_ref")
        if (
            not isinstance(self.source_sha256, str)
            or len(self.source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.source_sha256)
        ):
            raise ValueError("kernel source_sha256 must be a lowercase SHA-256 hex digest")
        if self.derived_cache is not True:
            raise ValueError("battle kernels must be marked derived_cache=true")

    def to_record(self) -> Dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "mean": self.mean.to_record(),
            "spread": self.spread.to_record(),
            "derived_cache": self.derived_cache,
        }


@dataclass(frozen=True)
class CombatIntent:
    action: str
    objective_ref: Optional[str] = None
    target_refs: Tuple[str, ...] = ()
    commitment_milli: int = 1000
    lethal_force_milli: int = 0
    resource_costs: Tuple[ResourceCost, ...] = ()
    destination_zone_ref: Optional[str] = None

    def __post_init__(self) -> None:
        if self.action not in INTENT_ACTIONS:
            raise ValueError(f"unsupported combat intent action: {self.action}")
        if self.objective_ref is not None:
            _reference(self.objective_ref, "intent objective_ref")
        object.__setattr__(
            self, "target_refs", _unique_references(self.target_refs, "intent target_ref")
        )
        _integer(self.commitment_milli, "commitment_milli", 0, 1000)
        _integer(self.lethal_force_milli, "lethal_force_milli", 0, 1000)
        costs = tuple(self.resource_costs)
        if len(costs) != len({cost.resource_ref for cost in costs}):
            raise ValueError("intent resource costs must have unique resource refs")
        object.__setattr__(self, "resource_costs", costs)
        if self.destination_zone_ref is not None:
            _reference(self.destination_zone_ref, "destination_zone_ref")

    def to_record(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "objective_ref": self.objective_ref,
            "target_refs": list(self.target_refs),
            "commitment_milli": self.commitment_milli,
            "lethal_force_milli": self.lethal_force_milli,
            "resource_costs": [cost.to_record() for cost in self.resource_costs],
            "destination_zone_ref": self.destination_zone_ref,
        }


@dataclass(frozen=True)
class InformationState:
    observed_refs: Tuple[str, ...]
    confidence_milli: int = 1000
    concealment_milli: int = 0
    surprise_milli: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observed_refs", _unique_references(self.observed_refs, "observed ref")
        )
        _integer(self.confidence_milli, "confidence_milli", 0, 1000)
        _integer(self.concealment_milli, "concealment_milli", 0, 1000)
        _integer(self.surprise_milli, "surprise_milli", 0, 1000)

    def to_record(self) -> Dict[str, Any]:
        return {
            "observed_refs": list(self.observed_refs),
            "confidence_milli": self.confidence_milli,
            "concealment_milli": self.concealment_milli,
            "surprise_milli": self.surprise_milli,
        }


@dataclass(frozen=True)
class PositionState:
    zone_ref: str
    elevation: int = 0
    cover_milli: int = 0

    def __post_init__(self) -> None:
        _reference(self.zone_ref, "position zone_ref")
        _integer(self.elevation, "position elevation", -100_000, 100_000)
        _integer(self.cover_milli, "position cover_milli", 0, 1000)

    def to_record(self) -> Dict[str, Any]:
        return {
            "zone_ref": self.zone_ref,
            "elevation": self.elevation,
            "cover_milli": self.cover_milli,
        }


@dataclass(frozen=True)
class Participant:
    participant_ref: str
    authoritative_owner_ref: str
    side_ref: str
    sequence: int
    representation: str
    capability: CapabilityProfile
    personnel: PersonnelState
    position: PositionState
    information: InformationState
    intent: CombatIntent
    initiative: int
    readiness: int
    morale: int
    cohesion: int
    resources: Tuple[ResourcePool, ...] = ()
    effective_range_bands: Tuple[int, ...] = (0,)
    kernel: Optional[BattleKernel] = None
    named_actor_refs: Tuple[str, ...] = ()
    specialist_refs: Tuple[str, ...] = ()
    unusual_technique_refs: Tuple[str, ...] = ()
    unusual_equipment_refs: Tuple[str, ...] = ()
    detailed_injury_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _reference(self.participant_ref, "participant_ref")
        _reference(self.authoritative_owner_ref, "authoritative_owner_ref")
        _reference(self.side_ref, "side_ref")
        _integer(self.sequence, "participant sequence", 0, 1_000_000_000)
        if self.representation not in REPRESENTATIONS:
            raise ValueError(f"unsupported participant representation: {self.representation}")
        _integer(self.initiative, "initiative", 0, 200)
        _integer(self.readiness, "readiness", 0, 200)
        _integer(self.morale, "morale", 0, 200)
        _integer(self.cohesion, "cohesion", 0, 200)
        resources = tuple(self.resources)
        if len(resources) != len({resource.resource_ref for resource in resources}):
            raise ValueError("participant resources must have unique resource refs")
        object.__setattr__(self, "resources", resources)
        ranges = tuple(self.effective_range_bands)
        if not ranges:
            raise ValueError("effective_range_bands must not be empty")
        for range_band in ranges:
            _integer(range_band, "effective range band", 0, 1000)
        if len(ranges) != len(set(ranges)):
            raise ValueError("effective_range_bands must be unique")
        object.__setattr__(self, "effective_range_bands", ranges)
        for field in (
            "named_actor_refs",
            "specialist_refs",
            "unusual_technique_refs",
            "unusual_equipment_refs",
            "detailed_injury_refs",
        ):
            object.__setattr__(
                self, field, _unique_references(getattr(self, field), field)
            )

    def to_record(self) -> Dict[str, Any]:
        return {
            "participant_ref": self.participant_ref,
            "authoritative_owner_ref": self.authoritative_owner_ref,
            "side_ref": self.side_ref,
            "sequence": self.sequence,
            "representation": self.representation,
            "capability": self.capability.to_record(),
            "personnel": self.personnel.to_record(),
            "position": self.position.to_record(),
            "information": self.information.to_record(),
            "intent": self.intent.to_record(),
            "initiative": self.initiative,
            "readiness": self.readiness,
            "morale": self.morale,
            "cohesion": self.cohesion,
            "resources": [resource.to_record() for resource in self.resources],
            "effective_range_bands": list(self.effective_range_bands),
            "kernel": None if self.kernel is None else self.kernel.to_record(),
            "named_actor_refs": list(self.named_actor_refs),
            "specialist_refs": list(self.specialist_refs),
            "unusual_technique_refs": list(self.unusual_technique_refs),
            "unusual_equipment_refs": list(self.unusual_equipment_refs),
            "detailed_injury_refs": list(self.detailed_injury_refs),
        }


@dataclass(frozen=True)
class SideTerrain:
    side_ref: str
    cover_milli: int = 1000
    mobility_milli: int = 1000
    visibility_milli: int = 1000
    hazard_milli: int = 0

    def __post_init__(self) -> None:
        _reference(self.side_ref, "terrain side_ref")
        _integer(self.cover_milli, "terrain cover_milli", 0, 2000)
        _integer(self.mobility_milli, "terrain mobility_milli", 0, 2000)
        _integer(self.visibility_milli, "terrain visibility_milli", 0, 2000)
        _integer(self.hazard_milli, "terrain hazard_milli", 0, 1000)

    def to_record(self) -> Dict[str, Any]:
        return {
            "side_ref": self.side_ref,
            "cover_milli": self.cover_milli,
            "mobility_milli": self.mobility_milli,
            "visibility_milli": self.visibility_milli,
            "hazard_milli": self.hazard_milli,
        }


@dataclass(frozen=True)
class TerrainState:
    terrain_ref: str
    side_modifiers: Tuple[SideTerrain, ...]

    def __post_init__(self) -> None:
        _reference(self.terrain_ref, "terrain_ref")
        modifiers = tuple(self.side_modifiers)
        if len(modifiers) != len({modifier.side_ref for modifier in modifiers}):
            raise ValueError("terrain side modifiers must have unique side refs")
        object.__setattr__(self, "side_modifiers", modifiers)

    def to_record(self) -> Dict[str, Any]:
        return {
            "terrain_ref": self.terrain_ref,
            "side_modifiers": [modifier.to_record() for modifier in self.side_modifiers],
        }


@dataclass(frozen=True)
class Engagement:
    engagement_ref: str
    actor_ref: str
    target_ref: str
    range_band: int
    line_of_sight: bool = True
    frontage_milli: int = 1000
    timing_delay_ms: int = 0

    def __post_init__(self) -> None:
        _reference(self.engagement_ref, "engagement_ref")
        _reference(self.actor_ref, "engagement actor_ref")
        _reference(self.target_ref, "engagement target_ref")
        if self.actor_ref == self.target_ref:
            raise ValueError("an engagement actor and target must differ")
        _integer(self.range_band, "engagement range_band", 0, 1000)
        if not isinstance(self.line_of_sight, bool):
            raise TypeError("line_of_sight must be boolean")
        _integer(self.frontage_milli, "engagement frontage_milli", 0, 2000)
        _integer(self.timing_delay_ms, "engagement timing_delay_ms", 0, 86_400_000)

    def to_record(self) -> Dict[str, Any]:
        return {
            "engagement_ref": self.engagement_ref,
            "actor_ref": self.actor_ref,
            "target_ref": self.target_ref,
            "range_band": self.range_band,
            "line_of_sight": self.line_of_sight,
            "frontage_milli": self.frontage_milli,
            "timing_delay_ms": self.timing_delay_ms,
        }


@dataclass(frozen=True)
class CombatObjective:
    objective_ref: str
    side_ref: str
    kind: str
    required_progress: int
    current_progress: int = 0
    target_refs: Tuple[str, ...] = ()
    primary: bool = True
    deadline_tick: Optional[int] = None
    zone_ref: Optional[str] = None

    def __post_init__(self) -> None:
        _reference(self.objective_ref, "objective_ref")
        _reference(self.side_ref, "objective side_ref")
        if self.kind not in OBJECTIVE_KINDS:
            raise ValueError(f"unsupported combat objective kind: {self.kind}")
        _integer(self.required_progress, "objective required_progress", 1, 1_000_000_000)
        _integer(
            self.current_progress,
            "objective current_progress",
            0,
            self.required_progress,
        )
        object.__setattr__(
            self, "target_refs", _unique_references(self.target_refs, "objective target_ref")
        )
        if not isinstance(self.primary, bool):
            raise TypeError("objective primary must be boolean")
        if self.deadline_tick is not None:
            _integer(self.deadline_tick, "objective deadline_tick", 0, 1_000_000_000)
        if self.zone_ref is not None:
            _reference(self.zone_ref, "objective zone_ref")
        if self.kind in ("hold", "secure") and self.zone_ref is None:
            raise ValueError(f"{self.kind} objective requires zone_ref")
        if self.kind == "delay" and self.deadline_tick is None:
            raise ValueError("delay objective requires deadline_tick")

    def to_record(self) -> Dict[str, Any]:
        return {
            "objective_ref": self.objective_ref,
            "side_ref": self.side_ref,
            "kind": self.kind,
            "required_progress": self.required_progress,
            "current_progress": self.current_progress,
            "target_refs": list(self.target_refs),
            "primary": self.primary,
            "deadline_tick": self.deadline_tick,
            "zone_ref": self.zone_ref,
        }


@dataclass(frozen=True)
class CombatTiming:
    current_tick: int
    exchange_seconds: int
    max_ticks: int

    def __post_init__(self) -> None:
        _integer(self.current_tick, "current_tick", 0, 1_000_000_000)
        _integer(self.exchange_seconds, "exchange_seconds", 1, 86_400)
        _integer(self.max_ticks, "max_ticks", 1, 1_000_000_000)
        if self.current_tick >= self.max_ticks:
            raise ValueError("current_tick must be less than max_ticks")

    def to_record(self) -> Dict[str, int]:
        return {
            "current_tick": self.current_tick,
            "exchange_seconds": self.exchange_seconds,
            "max_ticks": self.max_ticks,
        }


@dataclass(frozen=True)
class CombatContract:
    combat_ref: str
    transaction_ref: str
    scale: str
    participants: Tuple[Participant, ...]
    objectives: Tuple[CombatObjective, ...]
    engagements: Tuple[Engagement, ...]
    terrain: TerrainState
    timing: CombatTiming
    rng_stream: str
    rng_start_index: int = 0
    close_threshold: int = 10
    terrain_asymmetry_threshold_milli: int = 100

    def __post_init__(self) -> None:
        _reference(self.combat_ref, "combat_ref")
        _reference(self.transaction_ref, "transaction_ref")
        if self.scale not in SCALES:
            raise ValueError(f"unsupported combat scale: {self.scale}")
        _reference(self.rng_stream, "rng_stream")
        _integer(self.rng_start_index, "rng_start_index", 0, 1 << 63)
        _integer(self.close_threshold, "close_threshold", 0, 200)
        _integer(
            self.terrain_asymmetry_threshold_milli,
            "terrain_asymmetry_threshold_milli",
            0,
            2000,
        )
        participants = tuple(self.participants)
        objectives = tuple(self.objectives)
        engagements = tuple(self.engagements)
        if not 2 <= len(participants) <= 256:
            raise ValueError("combat requires 2..256 participants")
        if len(objectives) > 64:
            raise ValueError("combat allows at most 64 objectives")
        if len(engagements) > 1024:
            raise ValueError("combat allows at most 1024 engagements")
        if len({participant.participant_ref for participant in participants}) != len(participants):
            raise ValueError("participant refs must be unique")
        if len({participant.sequence for participant in participants}) != len(participants):
            raise ValueError("participant sequences must be unique explicit tie-breakers")
        if len({objective.objective_ref for objective in objectives}) != len(objectives):
            raise ValueError("objective refs must be unique")
        if len({engagement.engagement_ref for engagement in engagements}) != len(engagements):
            raise ValueError("engagement refs must be unique")
        participant_refs = {participant.participant_ref for participant in participants}
        side_refs = {participant.side_ref for participant in participants}
        objective_refs = {objective.objective_ref for objective in objectives}
        for participant in participants:
            if any(target_ref not in participant_refs for target_ref in participant.intent.target_refs):
                raise ValueError("intent target_refs must name combat participants")
            if any(observed_ref not in participant_refs for observed_ref in participant.information.observed_refs):
                raise ValueError("observed_refs must name combat participants")
            if (
                participant.intent.objective_ref is not None
                and participant.intent.objective_ref not in objective_refs
            ):
                raise ValueError("intent objective_ref must name a combat objective")
            available_resources = {resource.resource_ref for resource in participant.resources}
            if any(
                cost.resource_ref not in available_resources
                for cost in participant.intent.resource_costs
            ):
                raise ValueError("intent resource costs must name participant resources")
        for objective in objectives:
            if objective.side_ref not in side_refs:
                raise ValueError("objective side_ref must name a participating side")
            if any(target_ref not in participant_refs for target_ref in objective.target_refs):
                raise ValueError("objective target_refs must name combat participants")
        for engagement in engagements:
            if engagement.actor_ref not in participant_refs or engagement.target_ref not in participant_refs:
                raise ValueError("engagement endpoints must name combat participants")
            actor = next(
                participant
                for participant in participants
                if participant.participant_ref == engagement.actor_ref
            )
            if engagement.target_ref not in actor.intent.target_refs:
                raise ValueError("engagement target must be explicit in actor intent")
        terrain_sides = {modifier.side_ref for modifier in self.terrain.side_modifiers}
        if terrain_sides != side_refs:
            raise ValueError("terrain must provide exactly one modifier for every participating side")
        object.__setattr__(self, "participants", participants)
        object.__setattr__(self, "objectives", objectives)
        object.__setattr__(self, "engagements", engagements)

    def to_record(self) -> Dict[str, Any]:
        return {
            "combat_ref": self.combat_ref,
            "transaction_ref": self.transaction_ref,
            "scale": self.scale,
            "participants": [participant.to_record() for participant in self.participants],
            "objectives": [objective.to_record() for objective in self.objectives],
            "engagements": [engagement.to_record() for engagement in self.engagements],
            "terrain": self.terrain.to_record(),
            "timing": self.timing.to_record(),
            "rng_stream": self.rng_stream,
            "rng_start_index": self.rng_start_index,
            "close_threshold": self.close_threshold,
            "terrain_asymmetry_threshold_milli": self.terrain_asymmetry_threshold_milli,
        }


@dataclass(frozen=True)
class WakeTrigger:
    reason: str
    participant_ref: Optional[str] = None
    engagement_ref: Optional[str] = None

    def to_record(self) -> Dict[str, Any]:
        return {
            "reason": self.reason,
            "participant_ref": self.participant_ref,
            "engagement_ref": self.engagement_ref,
        }


@dataclass(frozen=True)
class ExchangeEffect:
    engagement_ref: str
    actor_ref: str
    target_ref: str
    action: str
    outcome: str
    perception_margin: int
    control_margin: int
    force_margin: int
    wounded: int = 0
    incapacitated: int = 0
    killed: int = 0
    captured: int = 0
    escaped: int = 0

    def to_record(self) -> Dict[str, Any]:
        return {
            "engagement_ref": self.engagement_ref,
            "actor_ref": self.actor_ref,
            "target_ref": self.target_ref,
            "action": self.action,
            "outcome": self.outcome,
            "perception_margin": self.perception_margin,
            "control_margin": self.control_margin,
            "force_margin": self.force_margin,
            "wounded": self.wounded,
            "incapacitated": self.incapacitated,
            "killed": self.killed,
            "captured": self.captured,
            "escaped": self.escaped,
        }


@dataclass(frozen=True)
class ParticipantEffect:
    participant_ref: str
    authoritative_owner_ref: str
    before_personnel: PersonnelState
    after_personnel: PersonnelState
    before_resources: Tuple[ResourcePool, ...]
    after_resources: Tuple[ResourcePool, ...]
    before_readiness: int
    after_readiness: int
    before_morale: int
    after_morale: int
    before_cohesion: int
    after_cohesion: int
    before_position: PositionState
    after_position: PositionState
    requires_partition: bool

    def to_record(self) -> Dict[str, Any]:
        return {
            "participant_ref": self.participant_ref,
            "authoritative_owner_ref": self.authoritative_owner_ref,
            "before_personnel": self.before_personnel.to_record(),
            "after_personnel": self.after_personnel.to_record(),
            "before_resources": [resource.to_record() for resource in self.before_resources],
            "after_resources": [resource.to_record() for resource in self.after_resources],
            "before_readiness": self.before_readiness,
            "after_readiness": self.after_readiness,
            "before_morale": self.before_morale,
            "after_morale": self.after_morale,
            "before_cohesion": self.before_cohesion,
            "after_cohesion": self.after_cohesion,
            "before_position": self.before_position.to_record(),
            "after_position": self.after_position.to_record(),
            "requires_partition": self.requires_partition,
        }


@dataclass(frozen=True)
class ObjectiveEffect:
    objective_ref: str
    side_ref: str
    before_progress: int
    after_progress: int
    achieved: bool

    def to_record(self) -> Dict[str, Any]:
        return {
            "objective_ref": self.objective_ref,
            "side_ref": self.side_ref,
            "before_progress": self.before_progress,
            "after_progress": self.after_progress,
            "achieved": self.achieved,
        }


@dataclass(frozen=True)
class SuccessorBoundary:
    kind: str
    at_tick: int
    participant_refs: Tuple[str, ...]
    authoritative_owner_refs: Tuple[str, ...]
    reason_code: str

    def to_record(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "at_tick": self.at_tick,
            "participant_refs": list(self.participant_refs),
            "authoritative_owner_refs": list(self.authoritative_owner_refs),
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class CombatEffectPlan:
    combat_ref: str
    transaction_ref: str
    scale: str
    resolution_mode: str
    wake_triggers: Tuple[WakeTrigger, ...]
    exchange_effects: Tuple[ExchangeEffect, ...]
    participant_effects: Tuple[ParticipantEffect, ...]
    objective_effects: Tuple[ObjectiveEffect, ...]
    victorious_side_refs: Tuple[str, ...]
    status: str
    successor_boundaries: Tuple[SuccessorBoundary, ...]
    rng_receipts: Tuple[DrawReceipt, ...]

    def __post_init__(self) -> None:
        if self.resolution_mode not in ("kernel", "detail"):
            raise ValueError("resolution_mode must be kernel or detail")
        if self.status not in ("ongoing", "completed"):
            raise ValueError("combat status must be ongoing or completed")
        if len(self.participant_effects) > 256:
            raise ValueError("effect plan participant bound exceeded")
        if len(self.exchange_effects) > 1024:
            raise ValueError("effect plan exchange bound exceeded")
        if len(self.objective_effects) > 64:
            raise ValueError("effect plan objective bound exceeded")
        if len(self.successor_boundaries) > 6:
            raise ValueError("effect plan successor bound exceeded")

    def to_record(self) -> Dict[str, Any]:
        return {
            "combat_ref": self.combat_ref,
            "transaction_ref": self.transaction_ref,
            "scale": self.scale,
            "resolution_mode": self.resolution_mode,
            "wake_triggers": [trigger.to_record() for trigger in self.wake_triggers],
            "exchange_effects": [effect.to_record() for effect in self.exchange_effects],
            "participant_effects": [effect.to_record() for effect in self.participant_effects],
            "objective_effects": [effect.to_record() for effect in self.objective_effects],
            "victorious_side_refs": list(self.victorious_side_refs),
            "status": self.status,
            "successor_boundaries": [boundary.to_record() for boundary in self.successor_boundaries],
            "rng_receipts": [
                {
                    "algorithm": receipt.algorithm,
                    "world_seed_hash": receipt.world_seed_hash,
                    "transaction_id": receipt.transaction_id,
                    "stream": receipt.stream,
                    "draw_index": receipt.draw_index,
                    "value_u64": receipt.value_u64,
                }
                for receipt in self.rng_receipts
            ],
        }
