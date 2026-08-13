from __future__ import annotations

from dataclasses import replace

import pytest

from shinobi_runtime.combat import (
    BattleKernel,
    CapabilityProfile,
    CombatContract,
    CombatIntent,
    CombatObjective,
    CombatRNGError,
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
from shinobi_runtime.sim import CounterRNG


ZERO_PROFILE = CapabilityProfile(0, 0, 0, 0, 0, 0, 0, 0, 0)
STRIKER_PROFILE = CapabilityProfile(180, 20, 180, 80, 180, 20, 200, 80, 20)
GUARD_PROFILE = CapabilityProfile(20, 20, 20, 20, 20, 20, 20, 20, 20)


def _kernel(profile: CapabilityProfile, digest_character: str) -> BattleKernel:
    return BattleKernel(
        source_ref=f"cache:{digest_character}",
        source_sha256=digest_character * 64,
        mean=profile,
        spread=ZERO_PROFILE,
    )


def _participant(
    *,
    ref: str,
    side: str,
    sequence: int,
    profile: CapabilityProfile,
    intent: CombatIntent,
    observed: tuple[str, ...],
    representation: str = "exact",
    kernel: BattleKernel | None = None,
    total: int = 10,
    resources: tuple[ResourcePool, ...] = (),
    position: str = "zone:field",
    named_actor_refs: tuple[str, ...] = (),
) -> Participant:
    return Participant(
        participant_ref=ref,
        authoritative_owner_ref=f"owner:{ref}",
        side_ref=side,
        sequence=sequence,
        representation=representation,
        capability=profile,
        kernel=kernel,
        personnel=PersonnelState(total=total, active=total),
        position=PositionState(zone_ref=position),
        information=InformationState(observed_refs=observed),
        intent=intent,
        initiative=100,
        readiness=100,
        morale=100,
        cohesion=100,
        resources=resources,
        effective_range_bands=(0, 1),
        named_actor_refs=named_actor_refs,
    )


def _contract(
    *,
    representation: str = "exact",
    scale: str = "duel",
    attacker_profile: CapabilityProfile = STRIKER_PROFILE,
    defender_profile: CapabilityProfile = GUARD_PROFILE,
    objectives: tuple[CombatObjective, ...] = (),
    attacker_intent: CombatIntent | None = None,
    attacker_total: int = 10,
    defender_total: int = 10,
    attacker_resources: tuple[ResourcePool, ...] = (),
    named_actor_refs: tuple[str, ...] = (),
) -> CombatContract:
    if attacker_intent is None:
        attacker_intent = CombatIntent(
            action="attack",
            target_refs=("participant:defender",),
            commitment_milli=1000,
            lethal_force_milli=250,
        )
    aggregate = representation == "aggregate"
    attacker = _participant(
        ref="participant:attacker",
        side="side:red",
        sequence=10,
        profile=attacker_profile,
        intent=attacker_intent,
        observed=("participant:defender",),
        representation=representation,
        kernel=_kernel(attacker_profile, "a") if aggregate else None,
        total=attacker_total,
        resources=attacker_resources,
        named_actor_refs=named_actor_refs,
    )
    defender = _participant(
        ref="participant:defender",
        side="side:blue",
        sequence=20,
        profile=defender_profile,
        intent=CombatIntent(action="hold"),
        observed=("participant:attacker",),
        representation=representation,
        kernel=_kernel(defender_profile, "b") if aggregate else None,
        total=defender_total,
    )
    return CombatContract(
        combat_ref="combat:test",
        transaction_ref="tx:test",
        scale=scale,
        participants=(attacker, defender),
        objectives=objectives,
        engagements=(
            Engagement(
                engagement_ref="engagement:attack",
                actor_ref=attacker.participant_ref,
                target_ref=defender.participant_ref,
                range_band=1,
            ),
        ),
        terrain=TerrainState(
            terrain_ref="terrain:plain",
            side_modifiers=(
                SideTerrain(side_ref="side:red"),
                SideTerrain(side_ref="side:blue"),
            ),
        ),
        timing=CombatTiming(current_tick=0, exchange_seconds=6, max_ticks=5),
        rng_stream="combat:test",
    )


def _receipts(contract: CombatContract):
    rng = CounterRNG(
        world_seed="test-world-seed",
        transaction_id=contract.transaction_ref,
        stream=contract.rng_stream,
        start_index=contract.rng_start_index,
    )
    for _ in range(required_draw_count(contract)):
        rng.draw_u64()
    return rng.receipts


def _mechanical_participant_projection(plan):
    return tuple(
        (
            effect.before_personnel,
            effect.after_personnel,
            effect.before_resources,
            effect.after_resources,
            effect.before_readiness,
            effect.after_readiness,
            effect.before_morale,
            effect.after_morale,
            effect.before_cohesion,
            effect.after_cohesion,
            effect.before_position,
            effect.after_position,
        )
        for effect in plan.participant_effects
    )


def test_representation_neutral_at_exact_kernel_boundary() -> None:
    exact = _contract(representation="exact", scale="formation")
    aggregate = _contract(representation="aggregate", scale="formation")

    exact_plan = resolve_combat(exact, _receipts(exact))
    aggregate_plan = resolve_combat(aggregate, _receipts(aggregate))

    assert exact_plan.resolution_mode == "detail"
    assert aggregate_plan.resolution_mode == "kernel"
    assert _mechanical_participant_projection(exact_plan) == _mechanical_participant_projection(
        aggregate_plan
    )
    assert tuple(
        (
            effect.action,
            effect.outcome,
            effect.perception_margin,
            effect.control_margin,
            effect.force_margin,
            effect.wounded,
            effect.incapacitated,
            effect.killed,
            effect.captured,
            effect.escaped,
        )
        for effect in exact_plan.exchange_effects
    ) == tuple(
        (
            effect.action,
            effect.outcome,
            effect.perception_margin,
            effect.control_margin,
            effect.force_margin,
            effect.wounded,
            effect.incapacitated,
            effect.killed,
            effect.captured,
            effect.escaped,
        )
        for effect in aggregate_plan.exchange_effects
    )


def test_close_kernel_threshold_deterministically_wakes_detail() -> None:
    contract = _contract(
        representation="aggregate",
        scale="battle",
        attacker_profile=GUARD_PROFILE,
        defender_profile=GUARD_PROFILE,
    )

    plan = resolve_combat(contract, _receipts(contract))

    assert plan.resolution_mode == "detail"
    assert any(
        trigger.reason == "close_threshold"
        and trigger.engagement_ref == "engagement:attack"
        for trigger in plan.wake_triggers
    )


def test_personnel_and_resources_are_conserved_in_bounded_effect_plan() -> None:
    resources = (ResourcePool("resource:chakra", capacity=10, current=7),)
    intent = CombatIntent(
        action="capture",
        objective_ref="objective:capture",
        target_refs=("participant:defender",),
        resource_costs=(ResourceCost("resource:chakra", amount=3),),
    )
    objective = CombatObjective(
        objective_ref="objective:capture",
        side_ref="side:red",
        kind="capture",
        target_refs=("participant:defender",),
        required_progress=5,
    )
    contract = _contract(
        representation="aggregate",
        scale="formation",
        objectives=(objective,),
        attacker_intent=intent,
        attacker_resources=resources,
        defender_total=20,
        named_actor_refs=("person:notable",),
    )

    plan = resolve_combat(contract, _receipts(contract))

    assert len(plan.participant_effects) <= len(contract.participants)
    assert len(plan.exchange_effects) <= len(contract.engagements)
    assert len(plan.objective_effects) <= len(contract.objectives)
    assert len(plan.successor_boundaries) <= 6
    for effect in plan.participant_effects:
        assert effect.authoritative_owner_ref
        assert sum(
            (
                effect.after_personnel.active,
                effect.after_personnel.wounded,
                effect.after_personnel.incapacitated,
                effect.after_personnel.killed,
                effect.after_personnel.captured,
                effect.after_personnel.escaped,
            )
        ) == effect.before_personnel.total
        assert effect.after_personnel.total == effect.before_personnel.total
        before_resources = {
            resource.resource_ref: resource for resource in effect.before_resources
        }
        for resource in effect.after_resources:
            assert resource.capacity == before_resources[resource.resource_ref].capacity
            assert resource.current <= before_resources[resource.resource_ref].current
    attacker_effect = plan.participant_effects[0]
    assert attacker_effect.after_resources[0].current == 4
    assert plan.rng_receipts == _receipts(contract)


def test_replay_is_byte_for_byte_deterministic_as_a_record() -> None:
    contract = _contract(representation="aggregate", scale="battle")
    receipts = _receipts(contract)

    first = resolve_combat(contract, receipts)
    second = resolve_combat(contract, receipts)

    assert first == second
    assert first.to_record() == second.to_record()


def test_primary_objective_drives_victory_with_opponent_survivors() -> None:
    objective = CombatObjective(
        objective_ref="objective:capture",
        side_ref="side:red",
        kind="capture",
        target_refs=("participant:defender",),
        required_progress=1,
    )
    intent = CombatIntent(
        action="capture",
        objective_ref=objective.objective_ref,
        target_refs=("participant:defender",),
    )
    contract = _contract(
        objectives=(objective,),
        attacker_intent=intent,
        attacker_total=2,
        defender_total=2,
        attacker_profile=CapabilityProfile(100, 100, 200, 100, 200, 0, 200, 100, 100),
        defender_profile=ZERO_PROFILE,
    )

    plan = resolve_combat(contract, _receipts(contract))

    defender = plan.participant_effects[1].after_personnel
    assert defender.captured >= 1
    assert defender.active >= 1
    assert plan.objective_effects[0].achieved
    assert plan.victorious_side_refs == ("side:red",)
    assert plan.status == "completed"
    assert plan.successor_boundaries[0].kind == "aftermath"


def test_reference_labels_never_supply_capability_or_tie_breaking() -> None:
    original = _contract(scale="duel")
    attacker, defender = original.participants
    renamed_attacker = replace(
        attacker,
        participant_ref="participant:apparently-weak",
        authoritative_owner_ref="owner:peasant-label",
        side_ref="side:renamed-red",
        information=replace(
            attacker.information,
            observed_refs=("participant:apparently-invincible",),
        ),
        intent=replace(
            attacker.intent,
            target_refs=("participant:apparently-invincible",),
        ),
    )
    renamed_defender = replace(
        defender,
        participant_ref="participant:apparently-invincible",
        authoritative_owner_ref="owner:kage-label",
        side_ref="side:renamed-blue",
        information=replace(
            defender.information,
            observed_refs=("participant:apparently-weak",),
        ),
    )
    renamed = replace(
        original,
        participants=(renamed_attacker, renamed_defender),
        engagements=(
            replace(
                original.engagements[0],
                engagement_ref="engagement:renamed",
                actor_ref=renamed_attacker.participant_ref,
                target_ref=renamed_defender.participant_ref,
            ),
        ),
        terrain=replace(
            original.terrain,
            terrain_ref="terrain:grandiose-label",
            side_modifiers=(
                replace(original.terrain.side_modifiers[0], side_ref="side:renamed-red"),
                replace(original.terrain.side_modifiers[1], side_ref="side:renamed-blue"),
            ),
        ),
    )

    original_plan = resolve_combat(original, _receipts(original))
    renamed_plan = resolve_combat(renamed, _receipts(renamed))

    original_exchange = original_plan.exchange_effects[0]
    renamed_exchange = renamed_plan.exchange_effects[0]
    assert (
        original_exchange.action,
        original_exchange.outcome,
        original_exchange.perception_margin,
        original_exchange.control_margin,
        original_exchange.force_margin,
        original_exchange.wounded,
        original_exchange.incapacitated,
        original_exchange.killed,
    ) == (
        renamed_exchange.action,
        renamed_exchange.outcome,
        renamed_exchange.perception_margin,
        renamed_exchange.control_margin,
        renamed_exchange.force_margin,
        renamed_exchange.wounded,
        renamed_exchange.incapacitated,
        renamed_exchange.killed,
    )
    assert _mechanical_participant_projection(original_plan) == _mechanical_participant_projection(
        renamed_plan
    )


def test_missing_or_noncontiguous_registered_draws_are_rejected() -> None:
    contract = _contract()
    receipts = _receipts(contract)

    with pytest.raises(CombatRNGError, match="exactly"):
        resolve_combat(contract, receipts[:-1])
    with pytest.raises(CombatRNGError, match="contiguous"):
        resolve_combat(
            contract,
            (receipts[0], replace(receipts[1], draw_index=99), receipts[2]),
        )


def test_action_outside_exchange_timing_is_deferred_without_spending() -> None:
    resources = (ResourcePool("resource:chakra", capacity=10, current=7),)
    contract = _contract(
        attacker_resources=resources,
        attacker_intent=CombatIntent(
            action="attack",
            target_refs=("participant:defender",),
            resource_costs=(ResourceCost("resource:chakra", amount=3),),
        ),
    )
    contract = replace(
        contract,
        engagements=(
            replace(
                contract.engagements[0],
                timing_delay_ms=contract.timing.exchange_seconds * 1000,
            ),
        ),
    )

    plan = resolve_combat(contract, _receipts(contract))

    assert plan.exchange_effects[0].outcome == "timing_deferred"
    assert plan.participant_effects[0].after_resources[0].current == 7
    assert plan.participant_effects[1].after_personnel == contract.participants[1].personnel


@pytest.mark.parametrize(
    ("participant_field", "reason"),
    (
        ("named_actor_refs", "named_actor"),
        ("specialist_refs", "specialist"),
        ("unusual_technique_refs", "unusual_technique"),
        ("unusual_equipment_refs", "unusual_equipment"),
        ("detailed_injury_refs", "detailed_injury"),
    ),
)
def test_detail_sensitive_metadata_wakes_aggregate_kernel(
    participant_field: str, reason: str
) -> None:
    contract = _contract(representation="aggregate", scale="battle")
    attacker, defender = contract.participants
    attacker = replace(attacker, **{participant_field: ("authority:explicit",)})
    contract = replace(contract, participants=(attacker, defender))

    plan = resolve_combat(contract, _receipts(contract))

    assert plan.resolution_mode == "detail"
    assert any(trigger.reason == reason for trigger in plan.wake_triggers)
