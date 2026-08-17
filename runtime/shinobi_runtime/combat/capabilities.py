"""Pure aggregate-capability projection and conservation helpers.

Formation component capability is persistent state.  These helpers project that
state into the generic combat kernel without turning a method proficiency into a
universal bonus, and move weighted sufficient statistics without cloning skill.
"""
from __future__ import annotations

from copy import deepcopy
from math import isqrt
from typing import Any, Dict, Mapping, Sequence, Tuple

from shinobi_runtime.combat.models import CapabilityProfile

FUNDAMENTAL_KEYS = (
    "combat", "awareness", "endurance", "chakra_control", "chakra_output",
    "movement", "tactics", "team_coordination",
)
METHOD_KEYS = (
    "sword", "unarmed", "thrown_tools", "bow", "polearm", "heavy_weapon",
    "ninjutsu", "genjutsu", "traps", "sensory", "medical", "sealing",
)


def _score(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(200, value))


def _state(state: Mapping[str, Any]) -> Tuple[Dict[str, int], Dict[str, int], int, int]:
    fundamentals = state.get("fundamentals")
    methods = state.get("methods")
    if not isinstance(fundamentals, Mapping) or not isinstance(methods, Mapping):
        raise ValueError("component capability state missing fundamentals/methods")
    f = {key: _score(fundamentals.get(key, 0)) for key in FUNDAMENTAL_KEYS}
    m = {key: _score(methods.get(key, 0)) for key in METHOD_KEYS}
    spread = state.get("spread", 10)
    experience = state.get("experience", 0)
    if isinstance(spread, bool) or not isinstance(spread, int):
        raise ValueError("component capability spread invalid")
    if isinstance(experience, bool) or not isinstance(experience, int):
        raise ValueError("component capability experience invalid")
    return f, m, max(1, min(50, spread)), max(0, min(200, experience))


def select_method(
    state: Mapping[str, Any],
    *,
    role: str,
    range_band: int,
    mechanics: Mapping[str, Any],
) -> Tuple[str, int]:
    """Choose the best *lawful* method for one component at this range.

    Equipment gates weapon methods.  The returned score is proficiency after
    range suitability, not a bonus added to general combat.
    """
    _f, methods, _spread, _experience = _state(state)
    preferences = mechanics.get("role_method_preferences")
    suitability = mechanics.get("method_range_suitability_milli")
    if not isinstance(preferences, Mapping) or not isinstance(suitability, Mapping):
        raise ValueError("formation capability mechanics invalid")
    candidates = preferences.get(role, preferences.get("assault", ()))
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes, bytearray)):
        raise ValueError("formation role method preferences invalid")
    available_raw = state.get("equipment_methods")
    if not isinstance(available_raw, Sequence) or isinstance(available_raw, (str, bytes, bytearray)):
        raise ValueError("component equipment method state invalid")
    available = {str(value) for value in available_raw}
    band = max(0, min(3, int(range_band)))
    best = ("unarmed", 0, -1)
    for priority, method in enumerate(candidates):
        if not isinstance(method, str) or method not in methods or method not in available:
            continue
        bands = suitability.get(method)
        if not isinstance(bands, Sequence) or isinstance(bands, (str, bytes, bytearray)) or len(bands) <= band:
            continue
        milli = bands[band]
        if isinstance(milli, bool) or not isinstance(milli, int) or milli < 0:
            continue
        effective = methods[method] * min(1000, milli) // 1000
        equipment = mechanics.get("equipment_projection")
        if isinstance(equipment, Mapping):
            dependent = equipment.get("equipment_dependent_methods")
            if (
                isinstance(dependent, Sequence)
                and not isinstance(dependent, (str, bytes, bytearray))
                and method in dependent
            ):
                readiness = state.get("equipment_readiness_milli", 1000)
                if isinstance(readiness, bool) or not isinstance(readiness, int):
                    raise ValueError("component equipment readiness invalid")
                floor = equipment.get("minimum_readiness_milli", 0)
                if isinstance(floor, bool) or not isinstance(floor, int):
                    floor = 0
                readiness = max(max(0, floor), min(1000, readiness))
                effective = effective * readiness // 1000
        candidate = (method, effective, -priority)
        if (candidate[1], candidate[2], candidate[0]) > (best[1], best[2], best[0]):
            best = candidate
    return best[0], best[1]


def project_component(
    state: Mapping[str, Any],
    *,
    role: str,
    action: str,
    range_band: int,
    mechanics: Mapping[str, Any],
) -> Tuple[CapabilityProfile, CapabilityProfile, int, str]:
    """Project one persistent component into combat axes for the chosen method."""
    f, methods, spread_value, experience = _state(state)
    method, method_score = select_method(state, role=role, range_band=range_band, mechanics=mechanics)
    combat = f["combat"]
    awareness = f["awareness"]
    endurance = f["endurance"]
    control = f["chakra_control"]
    output = f["chakra_output"]
    movement = f["movement"]
    tactics = f["tactics"]
    teamwork = f["team_coordination"]

    if method in ("ninjutsu", "genjutsu", "sealing"):
        offense = (35 * combat + 35 * method_score + 20 * output + 10 * control) // 100
    else:
        offense = (45 * combat + 35 * method_score + 10 * movement + 10 * awareness) // 100
    defense = (endurance + movement + control) // 3
    operational_control = (tactics + control + teamwork + method_score) // 4
    sensory = methods.get("sensory", awareness)
    perception = max(awareness, (awareness + sensory) // 2)
    stealth = (movement + tactics + awareness + max(methods.get("traps", 0), methods.get("genjutsu", 0))) // 4
    capture = (combat + method_score + control + tactics) // 4
    escape = (movement + awareness + tactics) // 3
    protection = (endurance + teamwork + control) // 3
    if action in ("hold", "secure", "delay"):
        defense = min(200, (2 * defense + operational_control) // 3 + experience // 20)
        protection = min(200, protection + experience // 25)
    elif action == "capture":
        capture = min(200, capture + experience // 20)
    elif action in ("escape", "extract", "disengage"):
        escape = min(200, escape + experience // 20)
    # Experience improves pressure-tested execution/decision quality only.  It
    # never rewrites the saved weapon/body proficiency here.
    operational_control = min(200, operational_control + experience // 20)
    profile = CapabilityProfile(
        offense=max(0, min(200, offense)),
        defense=max(0, min(200, defense)),
        control=max(0, min(200, operational_control)),
        mobility=movement,
        perception=perception,
        stealth=max(0, min(200, stealth)),
        capture=max(0, min(200, capture)),
        escape=max(0, min(200, escape)),
        protection=max(0, min(200, protection)),
    )
    spread = CapabilityProfile(**{key: spread_value for key in profile.to_record()})
    initiative = max(1, min(200, (awareness + tactics + movement + experience) // 4))
    return profile, spread, initiative, method


def weighted_state(states: Sequence[Tuple[Mapping[str, Any], int]]) -> Dict[str, Any]:
    """Personnel-weight capability sufficient statistics without duplication."""
    rows = [(state, int(weight)) for state, weight in states if int(weight) > 0]
    total = sum(weight for _state_row, weight in rows)
    if total <= 0:
        raise ValueError("weighted capability requires positive personnel")
    parsed = [(_state(state), state, weight) for state, weight in rows]
    fundamentals = {
        key: sum(values[0][key] * weight for values, _row, weight in parsed) // total
        for key in FUNDAMENTAL_KEYS
    }
    methods = {
        key: sum(values[1][key] * weight for values, _row, weight in parsed) // total
        for key in METHOD_KEYS
    }
    # Preserve within-group uncertainty and between-group separation.  A
    # single shared spread is intentionally coarse, but pooling second moments
    # prevents veteran/raw mixtures from looking artificially homogeneous.
    dimensions = tuple(FUNDAMENTAL_KEYS) + tuple(METHOD_KEYS)
    variance_numerator = 0
    for values, _row, weight in parsed:
        within = values[2] * values[2]
        deviations = [
            (values[0][key] - fundamentals[key]) ** 2 for key in FUNDAMENTAL_KEYS
        ] + [
            (values[1][key] - methods[key]) ** 2 for key in METHOD_KEYS
        ]
        between = sum(deviations) // max(1, len(dimensions))
        variance_numerator += (within + between) * weight
    spread = max(1, min(50, isqrt(max(1, variance_numerator // total))))
    experience = max(0, min(200, sum(values[3] * weight for values, _row, weight in parsed) // total))
    equipment = sorted({str(x) for _values, row, _weight in parsed for x in row.get("equipment_methods", ()) if isinstance(x, str)})
    equipment_readiness_milli = max(0, min(1000, sum(
        int(row.get("equipment_readiness_milli", 1000)) * weight
        for _values, row, weight in parsed
    ) // total))
    fundamental_bias = {
        key: sum(int(row.get("intake_fundamental_bias", {}).get(key, 0)) * weight for _values, row, weight in parsed) // total
        for key in FUNDAMENTAL_KEYS
    }
    method_bias = {
        key: sum(int(row.get("intake_method_bias", {}).get(key, 0)) * weight for _values, row, weight in parsed) // total
        for key in METHOD_KEYS
    }
    return {
        "source_capability_ref": "weighted_conserved_composition",
        "fundamentals": fundamentals,
        "methods": methods,
        "equipment_methods": equipment,
        "equipment_readiness_milli": equipment_readiness_milli,
        "intake_fundamental_bias": fundamental_bias,
        "intake_method_bias": method_bias,
        "spread": spread,
        "experience": experience,
        "development_evidence": {
            "combat_exchanges": sum(int(row.get("development_evidence", {}).get("combat_exchanges", 0)) * weight for _v, row, weight in parsed) // total,
            "mission_events": sum(int(row.get("development_evidence", {}).get("mission_events", 0)) * weight for _v, row, weight in parsed) // total,
            "training_hours": sum(int(row.get("development_evidence", {}).get("training_hours", 0)) * weight for _v, row, weight in parsed) // total,
            "last_event_ref": next((row.get("development_evidence", {}).get("last_event_ref") for _v, row, _weight in reversed(parsed) if row.get("development_evidence", {}).get("last_event_ref")), None),
        },
    }


def record_field_experience(state: Mapping[str, Any], *, event_ref: str, exchanges: int = 1) -> Dict[str, Any]:
    """Persist evidence/veterancy without handing out arbitrary skill points."""
    out = deepcopy(dict(state))
    evidence = out.setdefault("development_evidence", {})
    evidence["combat_exchanges"] = max(0, int(evidence.get("combat_exchanges", 0))) + max(1, int(exchanges))
    evidence["last_event_ref"] = event_ref
    current = _score(out.get("experience", 0))
    # Veterancy itself is an earned operational dimension.  It rises slowly and
    # has diminishing returns; technical skill waits for consolidation.
    gain = max(0, min(3, (201 - current) // 80))
    out["experience"] = min(200, current + gain)
    return out


def consolidate_training(state: Mapping[str, Any], *, hours: int, focus_methods: Sequence[str] = ()) -> Dict[str, Any]:
    """Convert training plus accumulated field evidence into bounded development."""
    out = deepcopy(dict(state))
    hours = max(1, int(hours))
    evidence = out.setdefault("development_evidence", {})
    field = max(0, int(evidence.get("combat_exchanges", 0)))
    evidence["training_hours"] = max(0, int(evidence.get("training_hours", 0))) + hours
    methods = out.get("methods")
    fundamentals = out.get("fundamentals")
    if not isinstance(methods, dict) or not isinstance(fundamentals, dict):
        raise ValueError("component capability state invalid")
    focus = [method for method in focus_methods if method in methods]
    if not focus:
        # Existing strongest combat method is the default drill focus; this
        # preserves institutional specialization instead of homogenizing forces.
        focus = [max(methods, key=lambda key: methods[key])] if methods else []
    training_units = max(1, hours // 4) + min(4, field // 3)
    for method in focus[:3]:
        value = _score(methods.get(method, 0))
        gain = min(3, max(0, training_units * max(10, 200 - value) // 600))
        methods[method] = min(200, value + gain)
    for key in ("tactics", "team_coordination"):
        value = _score(fundamentals.get(key, 0))
        gain = min(2, max(0, training_units * max(10, 200 - value) // 900))
        fundamentals[key] = min(200, value + gain)
    # Evidence is consumed by consolidation rather than farmed forever.
    evidence["combat_exchanges"] = max(0, field - min(field, max(1, hours // 4)))
    return out
