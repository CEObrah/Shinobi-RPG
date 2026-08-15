from shinobi_runtime.reducers.training import (
    ROUTINE_TRAINING_CEILING,
    TrainingInputs,
    point_cost,
    settle_training,
)


def _train(*, current: int, residual: str = "0", hours: str = "100000", representation: str = "exact"):
    return settle_training(
        TrainingInputs(
            scheduled_hours=hours,
            attendance="1",
            available_instructor_hours=hours,
            required_instructor_hours=hours,
            facility_slots="1",
            required_slots="1",
            equipment_sets="1",
            required_sets="1",
            instructor_quality_factor="1",
            facility_quality_factor="1",
            equipment_factor="1",
            health_factor="1",
            recovery_factor="1",
            relevance_factor="1",
            difficulty_fit_factor="1",
            aptitude=200,
            experience_modifier="1",
            current_value=current,
            residual_units=residual,
            representation=representation,
        )
    )


def test_routine_training_cannot_cross_legendary_boundary() -> None:
    outcome = _train(current=159)
    assert outcome.ending_value == ROUTINE_TRAINING_CEILING == 160
    assert outcome.points_gained == 1
    assert outcome.residual_units < point_cost(ROUTINE_TRAINING_CEILING)


def test_existing_exceptional_values_are_preserved_not_trained_higher() -> None:
    outcome = _train(current=185, residual="999999")
    assert outcome.ending_value == 185
    assert outcome.points_gained == 0
    assert outcome.earned_units == 0
    assert outcome.residual_units < point_cost(185)


def test_ceiling_is_representation_neutral() -> None:
    for representation in ("exact", "rostered_cohort", "aggregate"):
        assert _train(current=150, representation=representation).ending_value == 160
