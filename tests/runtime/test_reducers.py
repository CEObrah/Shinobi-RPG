from decimal import Decimal

import pytest

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
from shinobi_runtime.sim import CampaignTime


def training(representation: str) -> TrainingInputs:
    return TrainingInputs(
        scheduled_hours="6",
        attendance="1",
        available_instructor_hours="3",
        required_instructor_hours="6",
        facility_slots="10",
        required_slots="10",
        equipment_sets="10",
        required_sets="10",
        instructor_quality_factor="1.1",
        facility_quality_factor="1",
        equipment_factor="1",
        health_factor="1",
        recovery_factor="1",
        relevance_factor="1",
        difficulty_fit_factor="1",
        aptitude=120,
        experience_modifier="1",
        current_value=80,
        residual_units="0.2",
        representation=representation,
    )


def test_training_executes_declared_formula_and_is_representation_neutral():
    outcomes = [settle_training(training(mode)) for mode in (
        "exact", "rostered_cohort", "aggregate"
    )]
    assert outcomes[0] == outcomes[1] == outcomes[2]
    assert outcomes[0].instructor_access == Decimal("0.500")
    assert outcomes[0].capacity_factor == Decimal("0.500")
    assert outcomes[0].effective_hours == Decimal("3.300")
    assert outcomes[0].earned_units == Decimal("3.960")
    assert outcomes[0].ending_value == 81
    assert outcomes[0].residual_units == Decimal("1.160")


def test_training_residual_survives_without_integer_gain():
    inputs = training("exact")
    constrained = TrainingInputs(**{
        **inputs.__dict__,
        "scheduled_hours": "0.1",
        "current_value": 140,
        "residual_units": "0.125",
    })
    result = settle_training(constrained)
    assert result.ending_value == 140
    assert result.residual_units > Decimal("0.125")


def pool(pool_id, total, rank, availability):
    return PopulationPool(
        pool_id=pool_id,
        total=total,
        dimensions={"rank": rank, "availability": availability},
    )


def test_neutral_population_transfer_conserves_every_dimension():
    source = pool(
        "pool.fire",
        100,
        {"civilian": 80, "veteran": 20},
        {"available": 30, "committed": 70},
    )
    destination = pool(
        "pool.house",
        10,
        {"civilian": 10, "veteran": 0},
        {"available": 0, "committed": 10},
    )
    selected = neutral_proportional_selection(source, 10)
    transfer = PopulationTransfer(
        transfer_id="transfer.1",
        source_pool_id=source.pool_id,
        destination_pool_id=destination.pool_id,
        count=10,
        selected_dimensions=selected,
        selection_mode="neutral_proportional",
    )
    source_after, destination_after = apply_transfer(source, destination, transfer)
    assert source_after.total + destination_after.total == 110
    for dimension in source.dimensions:
        for category in set(source_after.dimensions[dimension]) | set(destination_after.dimensions[dimension]):
            before = source.dimensions[dimension].get(category, 0) + destination.dimensions[dimension].get(category, 0)
            after = source_after.dimensions[dimension].get(category, 0) + destination_after.dimensions[dimension].get(category, 0)
            assert after == before


def test_population_transfer_rejects_selected_people_not_in_source():
    source = pool("pool.fire", 2, {"civilian": 2}, {"available": 2})
    destination = pool("pool.house", 0, {"civilian": 0}, {"available": 0})
    transfer = PopulationTransfer(
        transfer_id="transfer.bad",
        source_pool_id=source.pool_id,
        destination_pool_id=destination.pool_id,
        count=1,
        selected_dimensions={"rank": {"veteran": 1}, "availability": {"available": 1}},
        selection_mode="explicit_selection",
    )
    with pytest.raises(ValueError, match="unknown selected category"):
        apply_transfer(source, destination, transfer)


def test_information_delivery_preserves_source_lineage_without_granting_truth():
    claim = InformationClaim(
        claim_id="claim.1",
        subject_ref="front.wave",
        source_ref="witness.1",
        collected_at=CampaignTime.parse("SE-0061-02-07T08:00:00"),
        epistemic_kind="observation",
        confidence_milli=900,
        fact_ref="fact.hidden",
        evidence_refs=("evidence.ledger",),
    )
    delivery = deliver_claim(
        claim,
        delivery_id="delivery.1",
        sender_ref="witness.1",
        recipient_ref="pc_wei_tang",
        channel="direct_report",
        delivered_at=CampaignTime.parse("SE-0061-02-07T08:05:00"),
        channel_confidence_milli=800,
    )
    assert delivery.resulting_epistemic_kind == "report"
    assert delivery.resulting_confidence_milli == 720
    assert delivery.evidence_refs == ("evidence.ledger",)
    assert "fact_ref" not in delivery.to_record()


def test_shared_health_reducer_applies_wound_to_exact_person_state() -> None:
    from types import SimpleNamespace
    from shinobi_runtime.reducers import apply_personnel_effect

    record = {
        "life_status": "alive",
        "resources": {"health": {"capacity": 100, "current": 100}},
        "condition": {"readiness": "ready", "injuries": []},
    }
    effect = SimpleNamespace(
        after_resources=(),
        after_personnel=SimpleNamespace(
            killed=False,
            incapacitated=False,
            wounded=True,
            captured=False,
            escaped=False,
        ),
    )
    apply_personnel_effect(record, effect=effect, event_marker="event.test")
    assert record["resources"]["health"]["current"] == 75
    assert record["condition"]["readiness"] == "injured"
    assert record["condition"]["injuries"] == ["event.test:wounded"]
