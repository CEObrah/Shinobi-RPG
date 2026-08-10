"""Executable representation-neutral training law.

The equations are the operative formulas currently declared in
``game/data/mechanics/training.json``.  Decimal arithmetic prevents model/runtime
variation and produces the repository's required half-up three-decimal result.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from typing import Any, Mapping, Tuple


_THREE = Decimal("0.001")
_ONE = Decimal(1)
_ZERO = Decimal(0)


def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _nonnegative(value: Any, name: str) -> Decimal:
    result = _decimal(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _factor(value: Any, name: str, *, capped: bool = False) -> Decimal:
    result = _nonnegative(value, name)
    if capped and result > 1:
        raise ValueError(f"{name} may not exceed 1")
    return result


@dataclass(frozen=True)
class TrainingInputs:
    scheduled_hours: Any
    attendance: Any
    available_instructor_hours: Any
    required_instructor_hours: Any
    facility_slots: Any
    required_slots: Any
    equipment_sets: Any
    required_sets: Any
    instructor_quality_factor: Any
    facility_quality_factor: Any
    equipment_factor: Any
    health_factor: Any
    recovery_factor: Any
    relevance_factor: Any
    difficulty_fit_factor: Any
    aptitude: int
    experience_modifier: Any
    current_value: int
    residual_units: Any = "0"
    representation: str = "exact"

    def __post_init__(self) -> None:
        if self.representation not in ("exact", "rostered_cohort", "aggregate"):
            raise ValueError("unsupported representation")
        if isinstance(self.aptitude, bool) or not isinstance(self.aptitude, int):
            raise TypeError("aptitude must be an integer")
        if self.aptitude < 0:
            raise ValueError("aptitude must be non-negative")
        if isinstance(self.current_value, bool) or not isinstance(self.current_value, int):
            raise TypeError("current_value must be an integer")
        if self.current_value < 0:
            raise ValueError("current_value must be non-negative")


@dataclass(frozen=True)
class TrainingOutcome:
    effective_hours: Decimal
    earned_units: Decimal
    starting_value: int
    ending_value: int
    points_gained: int
    residual_units: Decimal
    capacity_factor: Decimal
    instructor_access: Decimal

    def to_record(self) -> Mapping[str, Any]:
        return {
            "effective_hours": format(self.effective_hours, "f"),
            "earned_units": format(self.earned_units, "f"),
            "starting_value": self.starting_value,
            "ending_value": self.ending_value,
            "points_gained": self.points_gained,
            "residual_units": format(self.residual_units, "f"),
            "capacity_factor": format(self.capacity_factor, "f"),
            "instructor_access": format(self.instructor_access, "f"),
        }


def point_cost(value: int) -> int:
    return 1 + max(0, value - 40) // 20


def _access(available: Decimal, required: Decimal) -> Decimal:
    if required == 0:
        return _ONE
    return min(_ONE, available / required)


def settle_training(inputs: TrainingInputs) -> TrainingOutcome:
    with localcontext() as context:
        context.prec = 40
        scheduled = _nonnegative(inputs.scheduled_hours, "scheduled_hours")
        attendance = _factor(inputs.attendance, "attendance", capped=True)
        instructor_access = _access(
            _nonnegative(inputs.available_instructor_hours, "available_instructor_hours"),
            _nonnegative(inputs.required_instructor_hours, "required_instructor_hours"),
        )
        facility_access = _access(
            _nonnegative(inputs.facility_slots, "facility_slots"),
            _nonnegative(inputs.required_slots, "required_slots"),
        )
        equipment_access = _access(
            _nonnegative(inputs.equipment_sets, "equipment_sets"),
            _nonnegative(inputs.required_sets, "required_sets"),
        )
        capacity = min(instructor_access, facility_access, equipment_access)
        factors = (
            _factor(inputs.instructor_quality_factor, "instructor_quality_factor"),
            _factor(inputs.facility_quality_factor, "facility_quality_factor"),
            _factor(inputs.equipment_factor, "equipment_factor"),
            _factor(inputs.health_factor, "health_factor", capped=True),
            _factor(inputs.recovery_factor, "recovery_factor", capped=True),
            _factor(inputs.relevance_factor, "relevance_factor", capped=True),
            _factor(inputs.difficulty_fit_factor, "difficulty_fit_factor", capped=True),
        )
        effective = scheduled * attendance * capacity
        for factor in factors:
            effective *= factor

        learning_rate = Decimal(inputs.aptitude) / Decimal(100)
        experience = _factor(inputs.experience_modifier, "experience_modifier")
        overage = max(0, inputs.current_value - 100)
        diminishing = _ONE / (_ONE + Decimal(overage) / Decimal(100))
        earned = effective * learning_rate * experience * diminishing
        effective = effective.quantize(_THREE, rounding=ROUND_HALF_UP)
        earned = earned.quantize(_THREE, rounding=ROUND_HALF_UP)

        residual = _nonnegative(inputs.residual_units, "residual_units") + earned
        value = inputs.current_value
        gained = 0
        while residual >= point_cost(value):
            residual -= Decimal(point_cost(value))
            value += 1
            gained += 1
        residual = residual.quantize(_THREE, rounding=ROUND_HALF_UP)
        return TrainingOutcome(
            effective_hours=effective,
            earned_units=earned,
            starting_value=inputs.current_value,
            ending_value=value,
            points_gained=gained,
            residual_units=residual,
            capacity_factor=capacity.quantize(_THREE, rounding=ROUND_HALF_UP),
            instructor_access=instructor_access.quantize(_THREE, rounding=ROUND_HALF_UP),
        )
