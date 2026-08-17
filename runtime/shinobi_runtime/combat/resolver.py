"""Pure deterministic combat resolver shared by every supported scale.

There is one resolution contract for duels, skirmishes, formations, and battles.
Aggregate formation kernels are only a broad-phase source of the same capability axes; any
detail-sensitive condition deterministically wakes the exact path.  This
module does not import repository or persistence code and never writes files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from shinobi_runtime.sim.rng import DrawReceipt

from .models import (
    CapabilityProfile,
    CombatContract,
    CombatEffectPlan,
    ExchangeEffect,
    ObjectiveEffect,
    Participant,
    ParticipantEffect,
    PersonnelState,
    PositionState,
    ResourcePool,
    SideTerrain,
    SuccessorBoundary,
    WakeTrigger,
)


RNG_ALGORITHM = "sha256_counter_u64"


class CombatContractError(ValueError):
    """The supplied mechanical contract is incomplete or inconsistent."""


class CombatRNGError(CombatContractError):
    """Registered RNG receipts do not exactly satisfy the combat contract."""


@dataclass
class _MutableState:
    participant: Participant
    active: int
    wounded: int
    incapacitated: int
    killed: int
    captured: int
    escaped: int
    resources: Dict[str, int]
    readiness: int
    morale: int
    cohesion: int
    position: PositionState

    @classmethod
    def from_participant(cls, participant: Participant) -> "_MutableState":
        personnel = participant.personnel
        return cls(
            participant=participant,
            active=personnel.active,
            wounded=personnel.wounded,
            incapacitated=personnel.incapacitated,
            killed=personnel.killed,
            captured=personnel.captured,
            escaped=personnel.escaped,
            resources={resource.resource_ref: resource.current for resource in participant.resources},
            readiness=participant.readiness,
            morale=participant.morale,
            cohesion=participant.cohesion,
            position=participant.position,
        )

    def personnel(self) -> PersonnelState:
        return PersonnelState(
            total=self.participant.personnel.total,
            active=self.active,
            wounded=self.wounded,
            incapacitated=self.incapacitated,
            killed=self.killed,
            captured=self.captured,
            escaped=self.escaped,
        )

    def resource_pools(self) -> Tuple[ResourcePool, ...]:
        return tuple(
            ResourcePool(
                resource_ref=resource.resource_ref,
                capacity=resource.capacity,
                current=self.resources[resource.resource_ref],
            )
            for resource in self.participant.resources
        )


def required_draw_count(contract: CombatContract) -> int:
    """Return the exact number of raw registered draws the resolver consumes."""

    return len(contract.participants) + len(contract.engagements)


def _validate_receipts(
    contract: CombatContract, receipts: Sequence[DrawReceipt]
) -> Tuple[DrawReceipt, ...]:
    supplied = tuple(receipts)
    required = required_draw_count(contract)
    if len(supplied) != required:
        raise CombatRNGError(
            f"combat requires exactly {required} registered RNG draws; got {len(supplied)}"
        )
    seed_hash = None
    for offset, receipt in enumerate(supplied):
        if not isinstance(receipt, DrawReceipt):
            raise CombatRNGError("every RNG input must be a DrawReceipt")
        if receipt.algorithm != RNG_ALGORITHM:
            raise CombatRNGError("RNG receipt algorithm does not match combat contract")
        if receipt.transaction_id != contract.transaction_ref:
            raise CombatRNGError("RNG receipt transaction does not match combat contract")
        if receipt.stream != contract.rng_stream:
            raise CombatRNGError("RNG receipt stream does not match combat contract")
        expected_index = contract.rng_start_index + offset
        if receipt.draw_index != expected_index:
            raise CombatRNGError("RNG receipt draw indices must be contiguous and exact")
        if (
            not isinstance(receipt.world_seed_hash, str)
            or len(receipt.world_seed_hash) != 64
            or any(character not in "0123456789abcdef" for character in receipt.world_seed_hash)
        ):
            raise CombatRNGError("RNG receipt world_seed_hash is not a SHA-256 digest")
        if isinstance(receipt.value_u64, bool) or not isinstance(receipt.value_u64, int):
            raise CombatRNGError("RNG receipt value_u64 must be an integer")
        if not 0 <= receipt.value_u64 < (1 << 64):
            raise CombatRNGError("RNG receipt value_u64 is outside the unsigned 64-bit range")
        if seed_hash is None:
            seed_hash = receipt.world_seed_hash
        elif receipt.world_seed_hash != seed_hash:
            raise CombatRNGError("all combat RNG receipts must use one registered world seed")
    return supplied


def _terrain_by_side(contract: CombatContract) -> Dict[str, SideTerrain]:
    return {
        modifier.side_ref: modifier for modifier in contract.terrain.side_modifiers
    }


def _component_spread(profile: CapabilityProfile) -> int:
    return max(
        profile.offense,
        profile.defense,
        profile.control,
        profile.mobility,
        profile.perception,
        profile.stealth,
        profile.capture,
        profile.escape,
        profile.protection,
    )


def _wake_triggers(contract: CombatContract) -> Tuple[WakeTrigger, ...]:
    triggers: List[WakeTrigger] = []
    if contract.scale in ("duel", "skirmish"):
        triggers.append(WakeTrigger(reason="scale_requires_detail"))

    for participant in sorted(contract.participants, key=lambda item: item.sequence):
        participant_ref = participant.participant_ref
        if participant.representation != "aggregate":
            triggers.append(
                WakeTrigger(
                    reason="non_aggregate_participant",
                    participant_ref=participant_ref,
                )
            )
        elif participant.kernel is None:
            triggers.append(
                WakeTrigger(reason="missing_kernel", participant_ref=participant_ref)
            )
        for reason, references in (
            ("named_actor", participant.named_actor_refs),
            ("specialist", participant.specialist_refs),
            ("unusual_technique", participant.unusual_technique_refs),
            ("unusual_equipment", participant.unusual_equipment_refs),
            ("detailed_injury", participant.detailed_injury_refs),
        ):
            if references:
                triggers.append(
                    WakeTrigger(reason=reason, participant_ref=participant_ref)
                )

    terrain_values = tuple(contract.terrain.side_modifiers)
    for field in ("cover_milli", "mobility_milli", "visibility_milli", "hazard_milli"):
        values = [getattr(modifier, field) for modifier in terrain_values]
        if values and max(values) - min(values) >= contract.terrain_asymmetry_threshold_milli:
            triggers.append(WakeTrigger(reason="terrain_asymmetry"))
            break

    participants = {
        participant.participant_ref: participant for participant in contract.participants
    }
    broad_candidate = (
        contract.scale in ("formation", "battle")
        and all(
            participant.representation == "aggregate" and participant.kernel is not None
            for participant in contract.participants
        )
    )
    if broad_candidate:
        for engagement in contract.engagements:
            actor = participants[engagement.actor_ref]
            target = participants[engagement.target_ref]
            assert actor.kernel is not None and target.kernel is not None
            actor_mean = actor.kernel.mean
            target_mean = target.kernel.mean
            component_margins = (
                actor_mean.offense - ((2 * target_mean.defense + target_mean.protection) // 3),
                actor_mean.control - target_mean.mobility,
                actor_mean.perception - target_mean.stealth,
            )
            uncertainty = max(
                _component_spread(actor.kernel.spread),
                _component_spread(target.kernel.spread),
            )
            threshold = contract.close_threshold + uncertainty
            if all(abs(margin) <= threshold for margin in component_margins):
                triggers.append(
                    WakeTrigger(
                        reason="close_threshold",
                        engagement_ref=engagement.engagement_ref,
                    )
                )

    # Construction order is authoritative and deterministic; references never
    # decide mechanical priority.  Remove only exact duplicate trigger records.
    unique: List[WakeTrigger] = []
    seen = set()
    for trigger in triggers:
        key = (trigger.reason, trigger.participant_ref, trigger.engagement_ref)
        if key not in seen:
            seen.add(key)
            unique.append(trigger)
    return tuple(unique)


def _capabilities(
    contract: CombatContract, mode: str
) -> Dict[str, CapabilityProfile]:
    capabilities: Dict[str, CapabilityProfile] = {}
    for participant in contract.participants:
        if mode == "kernel":
            if participant.kernel is None:
                raise CombatContractError("kernel resolution requires every battle kernel")
            capabilities[participant.participant_ref] = participant.kernel.mean
        else:
            capabilities[participant.participant_ref] = participant.capability
    return capabilities


def _scale_milli(value: int, *modifiers: int) -> int:
    result = value
    for modifier in modifiers:
        result = (result * max(0, modifier)) // 1000
    return result


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return min(maximum, max(minimum, value))


def _resource_factor_and_spend(state: _MutableState) -> int:
    costs = state.participant.intent.resource_costs
    if not costs:
        return 1000
    factor = 1000
    for cost in costs:
        available = state.resources[cost.resource_ref]
        factor = min(factor, (available * 1000) // cost.amount)
    for cost in costs:
        available = state.resources[cost.resource_ref]
        state.resources[cost.resource_ref] = available - min(available, cost.amount)
    return factor


def _rounded_share(count: int, milli: int, draw: int) -> int:
    if count <= 0 or milli <= 0:
        return 0
    numerator = count * min(milli, 1000)
    result, remainder = divmod(numerator, 1000)
    if remainder and draw % 1000 < remainder:
        result += 1
    return min(count, result)


def _loss_count(actor_active: int, target_active: int, force_margin: int, effect_capacity: int = 1) -> int:
    exposed = min(actor_active * max(1, effect_capacity), target_active)
    pressure = max(0, force_margin + 25)
    if exposed == 0 or pressure == 0:
        return 0
    return min(target_active, (pressure * exposed + 2999) // 3000)


def _capture_count(actor_active: int, target_active: int, capture_margin: int, effect_capacity: int = 1) -> int:
    exposed = min(actor_active * max(1, effect_capacity), target_active)
    pressure = max(0, capture_margin + 30)
    if exposed == 0 or pressure == 0:
        return 0
    return min(target_active, (pressure * exposed + 2499) // 2500)


def _escape_count(actor_active: int, escape_margin: int) -> int:
    pressure = max(0, escape_margin + 50)
    if actor_active == 0 or pressure == 0:
        return 0
    return min(actor_active, (pressure * actor_active + 1999) // 2000)


def _margins(
    *,
    actor: _MutableState,
    target: _MutableState,
    actor_capability: CapabilityProfile,
    target_capability: CapabilityProfile,
    actor_terrain: SideTerrain,
    target_terrain: SideTerrain,
    range_band: int,
    frontage_milli: int,
    resource_factor: int,
    jitter: int,
) -> Tuple[int, int, int, int, int]:
    actor_info = _scale_milli(
        actor_capability.perception,
        actor.participant.information.confidence_milli,
        actor_terrain.visibility_milli,
    ) + actor.participant.information.surprise_milli // 20
    target_concealment = _scale_milli(
        target_capability.stealth,
        1000 + target.participant.information.concealment_milli,
    )
    perception_margin = actor_info - target_concealment + jitter

    actor_control = _scale_milli(
        actor_capability.control,
        actor.readiness * 5,
        actor.morale * 5,
        actor.participant.intent.commitment_milli,
        resource_factor,
    )
    target_mobility = _scale_milli(
        target_capability.mobility,
        target.readiness * 5,
        target.cohesion * 5,
        target_terrain.mobility_milli,
    )
    control_margin = actor_control - target_mobility + jitter

    range_factor = 1000 if range_band in actor.participant.effective_range_bands else 0
    actor_force = _scale_milli(
        actor_capability.offense,
        actor.readiness * 5,
        actor.participant.intent.commitment_milli,
        resource_factor,
        range_factor,
        frontage_milli,
    )
    target_defense_axis = (2 * target_capability.defense + target_capability.protection) // 3
    target_cover = _clamp(
        target.participant.position.cover_milli + target_terrain.cover_milli,
        0,
        2000,
    )
    target_defense = _scale_milli(
        target_defense_axis,
        target.readiness * 5,
        target_cover,
    )
    elevation_delta = actor.position.elevation - target.position.elevation
    force_margin = (
        actor_force
        - target_defense
        + perception_margin // 4
        + _clamp(elevation_delta, -20, 20)
        + jitter
    )

    capture_margin = (
        actor_capability.capture
        + actor_control
        - target_capability.escape
        - target_mobility
        + perception_margin // 4
        + jitter
    )
    escape_margin = (
        actor_capability.escape
        + _scale_milli(
            actor_capability.mobility,
            actor.readiness * 5,
            actor.cohesion * 5,
            actor_terrain.mobility_milli,
        )
        - target_capability.control
        - _scale_milli(
            target_capability.perception,
            target.participant.information.confidence_milli,
            target_terrain.visibility_milli,
        )
        + jitter
    )
    return perception_margin, control_margin, force_margin, capture_margin, escape_margin


def _apply_attack(
    actor: _MutableState,
    target: _MutableState,
    force_margin: int,
    control_margin: int,
    draw: int,
) -> Tuple[int, int, int]:
    losses = _loss_count(actor.active, target.active, force_margin, actor.participant.effect_capacity)
    killed = _rounded_share(
        losses, actor.participant.intent.lethal_force_milli, draw
    )
    remaining = losses - killed
    incapacitation_milli = _clamp(400 + control_margin * 4, 0, 1000)
    incapacitated = _rounded_share(remaining, incapacitation_milli, draw >> 12)
    wounded = remaining - incapacitated
    target.active -= losses
    target.killed += killed
    target.incapacitated += incapacitated
    target.wounded += wounded
    return wounded, incapacitated, killed


def _settle_condition_axes(state: _MutableState) -> None:
    before = state.participant.personnel
    new_harm = (
        state.wounded
        - before.wounded
        + state.incapacitated
        - before.incapacitated
        + state.killed
        - before.killed
        + state.captured
        - before.captured
    )
    if new_harm <= 0:
        return
    total = before.total
    morale_drop = min(200, (new_harm * 140 + total - 1) // total)
    cohesion_drop = min(200, (new_harm * 100 + total - 1) // total)
    readiness_drop = min(200, (new_harm * 80 + total - 1) // total)
    state.morale = max(0, state.morale - morale_drop)
    state.cohesion = max(0, state.cohesion - cohesion_drop)
    state.readiness = max(0, state.readiness - readiness_drop)


def _objective_effects(
    contract: CombatContract,
    states: Mapping[str, _MutableState],
    exchanges: Sequence[ExchangeEffect],
) -> Tuple[ObjectiveEffect, ...]:
    participant_by_ref = {
        participant.participant_ref: participant for participant in contract.participants
    }
    effects: List[ObjectiveEffect] = []
    next_tick = contract.timing.current_tick + 1
    for objective in contract.objectives:
        target_refs = set(objective.target_refs)
        addition = 0
        if objective.kind == "capture":
            addition = sum(
                exchange.captured
                for exchange in exchanges
                if participant_by_ref[exchange.actor_ref].side_ref == objective.side_ref
                and participant_by_ref[exchange.actor_ref].intent.objective_ref
                == objective.objective_ref
                and (not target_refs or exchange.target_ref in target_refs)
            )
        elif objective.kind == "eliminate":
            addition = sum(
                exchange.killed + exchange.incapacitated
                for exchange in exchanges
                if participant_by_ref[exchange.actor_ref].side_ref == objective.side_ref
                and participant_by_ref[exchange.actor_ref].intent.objective_ref
                == objective.objective_ref
                and (not target_refs or exchange.target_ref in target_refs)
            )
        elif objective.kind in ("escape", "extract", "disengage"):
            addition = sum(
                state.escaped - state.participant.personnel.escaped
                for state in states.values()
                if state.participant.side_ref == objective.side_ref
                and (
                    not target_refs
                    or state.participant.participant_ref in target_refs
                )
                and state.participant.intent.objective_ref == objective.objective_ref
                and state.participant.intent.action == objective.kind
            )
        elif objective.kind in ("hold", "secure"):
            addition = sum(
                state.active
                for state in states.values()
                if state.participant.side_ref == objective.side_ref
                and state.position.zone_ref == objective.zone_ref
                and state.participant.intent.objective_ref == objective.objective_ref
                and state.participant.intent.action == objective.kind
            )
        elif objective.kind == "delay":
            has_presence = any(
                state.active > 0
                and state.participant.side_ref == objective.side_ref
                and state.participant.intent.objective_ref == objective.objective_ref
                and state.participant.intent.action == "delay"
                for state in states.values()
            )
            if has_presence and objective.deadline_tick is not None and next_tick >= objective.deadline_tick:
                addition = objective.required_progress - objective.current_progress
        after = min(objective.required_progress, objective.current_progress + max(0, addition))
        effects.append(
            ObjectiveEffect(
                objective_ref=objective.objective_ref,
                side_ref=objective.side_ref,
                before_progress=objective.current_progress,
                after_progress=after,
                achieved=after >= objective.required_progress,
            )
        )
    return tuple(effects)


def _successors(
    contract: CombatContract,
    participant_effects: Sequence[ParticipantEffect],
    *,
    completed: bool,
) -> Tuple[SuccessorBoundary, ...]:
    next_tick = contract.timing.current_tick + 1
    buckets: Dict[str, List[ParticipantEffect]] = {}
    for effect in participant_effects:
        before = effect.before_personnel
        after = effect.after_personnel
        if after.wounded > before.wounded or after.incapacitated > before.incapacitated:
            buckets.setdefault("medical_settlement", []).append(effect)
        if after.captured > before.captured:
            buckets.setdefault("custody_transport", []).append(effect)
        if after.escaped > before.escaped:
            buckets.setdefault("pursuit_or_disengagement", []).append(effect)
        if effect.requires_partition:
            buckets.setdefault("formation_partition", []).append(effect)
        if effect.after_morale == 0 or effect.after_cohesion == 0:
            buckets.setdefault("morale_break", []).append(effect)

    primary_kind = "aftermath" if completed else "next_exchange"
    all_effects = list(participant_effects)
    buckets = {primary_kind: all_effects, **buckets}
    ordered_kinds = (
        primary_kind,
        "medical_settlement",
        "custody_transport",
        "pursuit_or_disengagement",
        "morale_break",
        "formation_partition",
    )
    successors: List[SuccessorBoundary] = []
    for kind in ordered_kinds:
        effects = buckets.get(kind)
        if not effects:
            continue
        participant_refs = tuple(effect.participant_ref for effect in effects)
        owner_refs = tuple(dict.fromkeys(effect.authoritative_owner_ref for effect in effects))
        successors.append(
            SuccessorBoundary(
                kind=kind,
                at_tick=next_tick,
                participant_refs=participant_refs,
                authoritative_owner_refs=owner_refs,
                reason_code=f"combat_{kind}",
            )
        )
    return tuple(successors)


def resolve_combat(
    contract: CombatContract, rng_receipts: Sequence[DrawReceipt]
) -> CombatEffectPlan:
    """Resolve one exchange and return a bounded, non-persisting effect plan.

    The function consumes only the receipts supplied by the caller.  It never
    generates randomness, inspects reference text, reads files, or writes state.
    """

    receipts = _validate_receipts(contract, rng_receipts)
    wake_triggers = _wake_triggers(contract)
    mode = "detail" if wake_triggers else "kernel"
    capabilities = _capabilities(contract, mode)
    terrain = _terrain_by_side(contract)

    ordered_participants = tuple(
        sorted(contract.participants, key=lambda participant: participant.sequence)
    )
    states: Dict[str, _MutableState] = {
        participant.participant_ref: _MutableState.from_participant(participant)
        for participant in ordered_participants
    }
    participant_draws = {
        participant.participant_ref: receipts[index].value_u64
        for index, participant in enumerate(ordered_participants)
    }
    engagement_offset = len(ordered_participants)
    engagement_draws = {
        engagement.engagement_ref: receipts[engagement_offset + index].value_u64
        for index, engagement in enumerate(contract.engagements)
    }

    # Costs are settled exactly once when a participant's initiative actually
    # reaches its first action.  An actor disabled earlier in the exchange does
    # not mysteriously consume resources after losing the ability to act.
    resource_factors: Dict[str, int] = {}

    initiative = {
        participant.participant_ref: (
            participant.initiative
            + participant.readiness // 4
            + int(participant_draws[participant.participant_ref] % 21)
            - 10
        )
        for participant in ordered_participants
    }
    participant_sequence = {
        participant.participant_ref: participant.sequence
        for participant in ordered_participants
    }
    ordered_engagements = tuple(
        engagement
        for _index, engagement in sorted(
            enumerate(contract.engagements),
            key=lambda indexed: (
                indexed[1].timing_delay_ms,
                -initiative[indexed[1].actor_ref],
                participant_sequence[indexed[1].actor_ref],
                indexed[0],
            ),
        )
    )

    exchanges: List[ExchangeEffect] = []
    for engagement in ordered_engagements:
        actor = states[engagement.actor_ref]
        target = states[engagement.target_ref]
        action = actor.participant.intent.action
        draw = engagement_draws[engagement.engagement_ref]
        jitter = int(draw % 21) - 10
        if engagement.timing_delay_ms >= contract.timing.exchange_seconds * 1000:
            exchanges.append(
                ExchangeEffect(
                    engagement_ref=engagement.engagement_ref,
                    actor_ref=engagement.actor_ref,
                    target_ref=engagement.target_ref,
                    action=action,
                    outcome="timing_deferred",
                    perception_margin=0,
                    control_margin=0,
                    force_margin=0,
                )
            )
            continue
        if engagement.actor_ref not in resource_factors:
            resource_factors[engagement.actor_ref] = (
                _resource_factor_and_spend(actor) if actor.active > 0 else 0
            )
        perception, control, force, capture_margin, escape_margin = _margins(
            actor=actor,
            target=target,
            actor_capability=capabilities[engagement.actor_ref],
            target_capability=capabilities[engagement.target_ref],
            actor_terrain=terrain[actor.participant.side_ref],
            target_terrain=terrain[target.participant.side_ref],
            range_band=engagement.range_band,
            frontage_milli=engagement.frontage_milli,
            resource_factor=resource_factors[engagement.actor_ref],
            jitter=jitter,
        )
        outcome = "no_effect"
        wounded = incapacitated = killed = captured = escaped = 0

        if actor.active <= 0:
            outcome = "actor_inactive"
        elif action in ("attack", "capture"):
            if target.active <= 0:
                outcome = "target_inactive"
            elif engagement.target_ref not in actor.participant.information.observed_refs:
                outcome = "not_observed"
            elif not engagement.line_of_sight:
                outcome = "no_line_of_sight"
            elif engagement.range_band not in actor.participant.effective_range_bands:
                outcome = "out_of_range"
            elif perception < -20:
                outcome = "not_detected"
            elif action == "attack":
                wounded, incapacitated, killed = _apply_attack(
                    actor, target, force, control, draw
                )
                outcome = "hit" if wounded + incapacitated + killed else "resisted"
            else:
                captured = _capture_count(actor.active, target.active, capture_margin, actor.participant.effect_capacity)
                target.active -= captured
                target.captured += captured
                outcome = "captured" if captured else "resisted"
        elif action in ("escape", "extract", "disengage"):
            escaped = _escape_count(actor.active, escape_margin)
            actor.active -= escaped
            actor.escaped += escaped
            if escaped and actor.participant.intent.destination_zone_ref is not None:
                actor.position = PositionState(
                    zone_ref=actor.participant.intent.destination_zone_ref,
                    elevation=actor.position.elevation,
                    cover_milli=actor.position.cover_milli,
                )
            outcome = "escaped" if escaped else "contained"
        else:
            # Hold/secure/delay are settled from explicit intent and position in
            # objective processing.  Their engagement has no inferred attack.
            outcome = "position_maintained"

        exchanges.append(
            ExchangeEffect(
                engagement_ref=engagement.engagement_ref,
                actor_ref=engagement.actor_ref,
                target_ref=engagement.target_ref,
                action=action,
                outcome=outcome,
                perception_margin=perception,
                control_margin=control,
                force_margin=force,
                wounded=wounded,
                incapacitated=incapacitated,
                killed=killed,
                captured=captured,
                escaped=escaped,
            )
        )

    # Positional intents need no opposing engagement, but they still have one
    # explicit action cost.  Inactive participants do not act or spend it.
    for participant in ordered_participants:
        if (
            participant.participant_ref not in resource_factors
            and participant.intent.action in ("hold", "secure", "delay")
        ):
            state = states[participant.participant_ref]
            resource_factors[participant.participant_ref] = (
                _resource_factor_and_spend(state) if state.active > 0 else 0
            )

    for state in states.values():
        _settle_condition_axes(state)

    participant_effects: List[ParticipantEffect] = []
    for participant in ordered_participants:
        state = states[participant.participant_ref]
        after_personnel = state.personnel()
        moved_from_active = participant.personnel.active - after_personnel.active
        requires_partition = (
            participant.representation == "aggregate"
            and moved_from_active > 0
            and after_personnel.active > 0
        )
        participant_effects.append(
            ParticipantEffect(
                participant_ref=participant.participant_ref,
                authoritative_owner_ref=participant.authoritative_owner_ref,
                before_personnel=participant.personnel,
                after_personnel=after_personnel,
                before_resources=participant.resources,
                after_resources=state.resource_pools(),
                before_readiness=participant.readiness,
                after_readiness=state.readiness,
                before_morale=participant.morale,
                after_morale=state.morale,
                before_cohesion=participant.cohesion,
                after_cohesion=state.cohesion,
                before_position=participant.position,
                after_position=state.position,
                requires_partition=requires_partition,
            )
        )

    objective_effects = _objective_effects(contract, states, exchanges)
    primary_by_side: Dict[str, List[ObjectiveEffect]] = {}
    primary_refs = {
        objective.objective_ref
        for objective in contract.objectives
        if objective.primary
    }
    for effect in objective_effects:
        if effect.objective_ref in primary_refs:
            primary_by_side.setdefault(effect.side_ref, []).append(effect)
    side_order = tuple(
        dict.fromkeys(participant.side_ref for participant in ordered_participants)
    )
    victorious = tuple(
        side_ref
        for side_ref in side_order
        if primary_by_side.get(side_ref)
        and all(effect.achieved for effect in primary_by_side[side_ref])
    )
    timed_out = contract.timing.current_tick + 1 >= contract.timing.max_ticks
    completed = bool(victorious) or timed_out
    successors = _successors(
        contract, participant_effects, completed=completed
    )

    return CombatEffectPlan(
        combat_ref=contract.combat_ref,
        transaction_ref=contract.transaction_ref,
        scale=contract.scale,
        resolution_mode=mode,
        wake_triggers=wake_triggers,
        exchange_effects=tuple(exchanges),
        participant_effects=tuple(participant_effects),
        objective_effects=objective_effects,
        victorious_side_refs=victorious,
        status="completed" if completed else "ongoing",
        successor_boundaries=successors,
        rng_receipts=receipts,
    )
