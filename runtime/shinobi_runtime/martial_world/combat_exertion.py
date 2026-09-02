"""Physical exertion accounting for exact Jianghu combat.

Fatigue is a current physiological burden, not an action log.  Work is priced
from the physical action being performed and then divided by Endurance.  A
fighter with 200 Endurance therefore has about five times the sustained work
capacity of an otherwise identical fighter with 40 Endurance; there is no
hidden high-stat plateau.
"""
from __future__ import annotations

from fractions import Fraction

import math
from typing import Any, Mapping

_REFERENCE_ENDURANCE = 80
_FATIGUE_MAX_PENALTY_MILLI = 3000
_FATIGUE_PERFORMANCE_FLOOR_MILLI = 250


def fatigue_performance_milli(fatigue_milli: int) -> int:
    """Whole-body combat performance remaining under current fatigue.

    ``fatigue_milli`` is normalized physiological burden: 0 is fresh and 3000
    reaches the maximum combat-performance penalty. Burden may exceed 3000 as
    recovery debt, but severe exhaustion must not erase every difference in
    skill, speed, perception, strength, or dexterity. A 250-milli floor keeps
    exhausted fighters badly degraded while preserving relative capability so
    elite and ordinary combatants do not collapse to the same zero-stat actor.
    Endurance changes how quickly burden accumulates; it does not create a
    second hidden fatigue scale.
    """
    fatigue = max(0, int(fatigue_milli))
    if fatigue >= _FATIGUE_MAX_PENALTY_MILLI:
        return _FATIGUE_PERFORMANCE_FLOOR_MILLI
    usable = 1000 - _FATIGUE_PERFORMANCE_FLOOR_MILLI
    penalty = fatigue * usable // _FATIGUE_MAX_PENALTY_MILLI
    return max(_FATIGUE_PERFORMANCE_FLOOR_MILLI, 1000 - penalty)


def _ceil_div(num: int, den: int) -> int:
    return (max(0, int(num)) + max(1, int(den)) - 1) // max(1, int(den))


def action_work_points(
    *, action_kind: str, person: Mapping[str, Any], weapon: Mapping[str, Any] | None,
    commitment_milli: int,
) -> int:
    """Return reference-Endurance work for one committed physical action.

    The action family determines how much of the body/weapon must be
    accelerated.  Weapon mass is therefore not interchangeable with carried
    encumbrance: a needle in a pouch contributes almost nothing to release work,
    while sweeping a long staff or glaive is deliberately expensive.
    """
    commitment = max(0, min(1000, int(commitment_milli)))
    base = 4 + _ceil_div(commitment, 160)
    body_mass_kg = max(1.0, float(person.get("body_mass_kg", 70) or 70))
    mass_kg = max(0.0, float((weapon or {}).get("mass_kg", 0) or 0))

    if action_kind in {"unarmed_strike", "clinch", "grapple_control", "grapple_escape", "takedown", "throw", "joint_lock"}:
        # Gross unarmed actions accelerate a meaningful portion of the body.
        factor = {
            "unarmed_strike": 0.15,
            "clinch": 0.11,
            "grapple_control": 0.10,
            "grapple_escape": 0.12,
            "takedown": 0.16,
            "throw": 0.20,
            "joint_lock": 0.08,
        }.get(action_kind, 0.12)
        return max(1, base + int(math.ceil(body_mass_kg * factor)))
    if action_kind == "hidden_weapon_throw":
        return max(1, base + 1 + int(math.ceil(mass_kg * 6.0)))
    if action_kind == "bow_shot":
        draw_kgf = max(0.0, float((weapon or {}).get("draw_weight_kgf", 0) or 0))
        return max(1, base + int(math.ceil(draw_kgf * 0.12)))
    if action_kind == "polearm_sweep":
        return max(1, base + int(math.ceil(mass_kg * 9.0)))
    if action_kind in {"polearm_strike", "cut"}:
        return max(1, base + int(math.ceil(mass_kg * 6.0)))
    if action_kind in {"polearm_thrust", "polearm_butt_strike", "thrust"}:
        return max(1, base + int(math.ceil(mass_kg * 5.0)))
    if action_kind in {"projected_qi_body", "projected_qi_weapon"}:
        # Projection is mainly metabolic/Qi strain.  The physical release itself
        # remains small; Qi expenditure is accounted separately.
        return max(1, base + 2)
    return max(1, base + int(math.ceil(mass_kg * 5.0)))


def defense_work_points(*, response: str, movement_mm: int = 0) -> int:
    """Reference-Endurance work for an active defensive response."""
    base = {
        "none": 0,
        "evade": 8,
        "dodge": 8,
        "reposition": 7,
        "parry": 5,
        "deflect": 4,
        "block": 6,
        "brace": 4,
        "counter_intercept": 8,
    }.get(str(response), 5 if response else 0)
    translation = _ceil_div(max(0, int(movement_mm)), 1500)
    return max(0, base + translation)


def movement_work_points(*, distance_mm: int, mounted: bool) -> int:
    """Reference-Endurance work for committed closing/retreat movement."""
    distance = max(0, int(distance_mm))
    if distance <= 0:
        return 0
    mm_per_work = 4000 if mounted else 1200
    return _ceil_div(distance, mm_per_work)


def fatigue_cost(
    *, raw_work_points: int, endurance: int, load_factor_milli: int = 1000,
) -> int:
    """Convert reference work into current fatigue burden.

    Endurance is intentionally inverse-linear here.  Training rarity and the
    difficulty of raising Endurance provide progression diminishing returns;
    the stat's own physical meaning is not secretly saturated.
    """
    raw = max(0, int(raw_work_points))
    if raw <= 0:
        return 0
    e = max(1, int(endurance))
    load = max(500, min(5000, int(load_factor_milli)))
    numerator = raw * _REFERENCE_ENDURANCE * load
    return max(1, _ceil_div(numerator, e * 1000))


def apply_exertion(
    person: dict[str, Any], *, raw_work_points: int, load_factor_milli: int = 1000,
) -> int:
    attrs = person.get("attributes", {}) if isinstance(person.get("attributes"), Mapping) else {}
    cost = fatigue_cost(
        raw_work_points=raw_work_points,
        endurance=max(1, int(attrs.get("endurance", 0) or 0)),
        load_factor_milli=load_factor_milli,
    )
    if cost:
        # Never clamp here. Other systems may lawfully create fatigue above the
        # range where combat penalties have already bottomed out; exertion must
        # never heal a more-fatigued person by applying a ceiling.
        person["fatigue_milli"] = max(0, int(person.get("fatigue_milli", 0))) + cost
    return cost


def guard_work_fraction(
    *, elapsed_ms: int, endurance: int, held_mass_kg: float, load_factor_milli: int = 1000,
) -> Fraction:
    """Exact fractional guard work in the same sub-fatigue units as ``guard_work_milli``."""
    if elapsed_ms <= 0:
        return Fraction(0, 1)
    # 25 milli-fatigue/s baseline at E80, plus 10 per kg held in guard.
    rate = 25 + int(round(max(0.0, float(held_mass_kg)) * 10.0))
    load = max(500, min(5000, int(load_factor_milli)))
    e = max(1, int(endurance))
    return Fraction(rate * int(elapsed_ms) * _REFERENCE_ENDURANCE * load, 1000 * e * 1000)


def guard_work_milli(
    *, elapsed_ms: int, endurance: int, held_mass_kg: float, load_factor_milli: int = 1000,
) -> int:
    """Whole sub-fatigue units from continuously maintaining a live guard.

    Persistent combat uses ``guard_work_fraction`` so repeated short clock
    advances conserve the fractional remainder exactly. This compatibility
    projection keeps the former integer API for direct callers.
    """
    return max(0, int(guard_work_fraction(
        elapsed_ms=elapsed_ms,endurance=endurance,held_mass_kg=held_mass_kg,load_factor_milli=load_factor_milli,
    )))


__all__ = [
    "action_work_points", "apply_exertion", "defense_work_points", "fatigue_cost",
    "fatigue_performance_milli", "guard_work_fraction", "guard_work_milli", "movement_work_points",
]
