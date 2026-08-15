import json

import pytest

from shinobi_runtime.reducers import (
    Mission,
    MissionObjective,
    MissionTransitionError,
    ObjectiveDependencyError,
    SettlementConflictError,
    SettlementTerm,
    derive_mission_outcome,
    settle_mission,
    transition_mission,
    update_objective,
)


def offered_mission() -> Mission:
    return Mission(
        mission_id="mission.broken_ledger",
        state="offered",
        participant_refs=(
            "team.black_hound",
            "pc_wei_tang",
            "formation.konoha_courier",
        ),
        objectives=(
            MissionObjective(
                objective_id="obj.locate_courier",
                kind="identify",
                required=True,
            ),
            MissionObjective(
                objective_id="obj.recover_ledger",
                kind="recover",
                required=True,
                dependencies=("obj.locate_courier",),
            ),
            MissionObjective(
                objective_id="obj.capture_handler",
                kind="capture",
                required=False,
                dependencies=("obj.locate_courier",),
            ),
        ),
        settlement_terms=(
            SettlementTerm(
                term_id="term.base_reward",
                direction="reward",
                account_ref="house.tang",
                asset_ref="currency.ryo",
                quantity=5000,
                applies_on=("succeeded",),
            ),
            SettlementTerm(
                term_id="term.capture_bonus",
                direction="reward",
                account_ref="house.tang",
                asset_ref="currency.ryo",
                quantity=1000,
                applies_on=("succeeded",),
                objective_id="obj.capture_handler",
                objective_status="succeeded",
            ),
            SettlementTerm(
                term_id="term.supply_cost",
                direction="cost",
                account_ref="house.tang",
                asset_ref="inventory.field_supplies",
                quantity=1,
                applies_on=("succeeded", "failed", "aborted", "expired"),
            ),
        ),
    )


def active_mission() -> Mission:
    mission = transition_mission(offered_mission(), "accepted")
    return transition_mission(mission, "active")


def succeed_objective(mission: Mission, objective_id: str, evidence: str) -> Mission:
    return update_objective(
        mission,
        objective_id,
        "succeeded",
        resolution_ref=evidence,
    )


def test_illegal_mission_transitions_and_manual_outcomes_are_rejected() -> None:
    mission = offered_mission()
    with pytest.raises(MissionTransitionError, match="illegal mission transition"):
        transition_mission(mission, "active")
    with pytest.raises(MissionTransitionError, match="must be derived"):
        transition_mission(mission, "succeeded")

    accepted = transition_mission(mission, "accepted")
    aborted = transition_mission(
        accepted,
        "aborted",
        reason_ref="event.client_withdrew_authority",
    )
    assert aborted.state == "aborted"
    with pytest.raises(MissionTransitionError, match="illegal mission transition"):
        transition_mission(aborted, "active")


def test_objective_dependency_gate_requires_successful_dependencies() -> None:
    mission = active_mission()
    with pytest.raises(ObjectiveDependencyError, match="obj.locate_courier"):
        update_objective(
            mission,
            "obj.recover_ledger",
            "in_progress",
            progress_milli=100,
        )

    mission = succeed_objective(
        mission,
        "obj.locate_courier",
        "event.courier_identified",
    )
    mission = update_objective(
        mission,
        "obj.recover_ledger",
        "in_progress",
        progress_milli=400,
    )
    assert mission.objective_by_id["obj.recover_ledger"].progress_milli == 400
    with pytest.raises(MissionTransitionError, match="nondecreasing"):
        update_objective(
            mission,
            "obj.recover_ledger",
            "in_progress",
            progress_milli=399,
        )


def test_required_success_derives_success_despite_failed_optional_objective() -> None:
    mission = active_mission()
    mission = succeed_objective(
        mission,
        "obj.locate_courier",
        "event.courier_identified",
    )
    mission = succeed_objective(
        mission,
        "obj.recover_ledger",
        "event.ledger_recovered",
    )
    mission = update_objective(
        mission,
        "obj.capture_handler",
        "in_progress",
        progress_milli=300,
    )
    mission = update_objective(
        mission,
        "obj.capture_handler",
        "failed",
        progress_milli=300,
        resolution_ref="event.handler_escaped",
    )
    mission = transition_mission(mission, "resolving")
    mission = derive_mission_outcome(mission)

    assert mission.state == "succeeded"
    assert mission.objective_by_id["obj.capture_handler"].status == "failed"
    settlement = settle_mission(mission, "settle.broken_ledger.001")
    assert settlement.applied
    assert settlement.settlement.reward_term_ids == ("term.base_reward",)
    assert settlement.settlement.cost_term_ids == ("term.supply_cost",)


def test_required_failure_beats_partial_progress_and_optional_success() -> None:
    mission = active_mission()
    mission = succeed_objective(
        mission,
        "obj.locate_courier",
        "event.courier_identified",
    )
    mission = succeed_objective(
        mission,
        "obj.capture_handler",
        "event.handler_captured",
    )
    mission = update_objective(
        mission,
        "obj.recover_ledger",
        "failed",
        progress_milli=700,
        resolution_ref="event.ledger_destroyed",
    )
    mission = transition_mission(mission, "resolving")
    mission = derive_mission_outcome(mission)
    assert mission.state == "failed"

    settlement = settle_mission(mission, "settle.broken_ledger.failed")
    assert settlement.settlement.reward_term_ids == ()
    assert settlement.settlement.cost_term_ids == ("term.supply_cost",)


def test_settlement_token_is_exactly_once_and_conflicting_token_fails() -> None:
    mission = active_mission()
    mission = succeed_objective(
        mission,
        "obj.locate_courier",
        "event.courier_identified",
    )
    mission = succeed_objective(
        mission,
        "obj.recover_ledger",
        "event.ledger_recovered",
    )
    mission = succeed_objective(
        mission,
        "obj.capture_handler",
        "event.handler_captured",
    )
    mission = derive_mission_outcome(transition_mission(mission, "resolving"))

    first = settle_mission(mission, "settle.broken_ledger.001")
    assert first.applied
    assert first.settlement.reward_term_ids == (
        "term.base_reward",
        "term.capture_bonus",
    )
    duplicate = settle_mission(
        first.mission,
        "settle.broken_ledger.001",
    )
    assert not duplicate.applied
    assert duplicate.mission == first.mission
    assert duplicate.settlement == first.settlement
    with pytest.raises(SettlementConflictError, match="different token"):
        settle_mission(first.mission, "settle.broken_ledger.002")


def test_record_round_trip_is_deterministic_and_json_serializable() -> None:
    mission = active_mission()
    mission = succeed_objective(
        mission,
        "obj.locate_courier",
        "event.courier_identified",
    )
    mission = succeed_objective(
        mission,
        "obj.recover_ledger",
        "event.ledger_recovered",
    )
    mission = derive_mission_outcome(transition_mission(mission, "resolving"))
    mission = settle_mission(mission, "settle.broken_ledger.001").mission

    record = mission.to_record()
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
    decoded = json.loads(encoded)
    restored = Mission.from_record(decoded)
    assert restored == mission
    assert restored.to_record() == record
    assert restored.participant_refs == tuple(sorted(restored.participant_refs))
    assert not any(
        key in encoded.lower()
        for key in ("narration", "description", "inference")
    )
