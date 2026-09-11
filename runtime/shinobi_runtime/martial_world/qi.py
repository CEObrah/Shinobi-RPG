"""Single executable authority for Jianghu Qi mechanics.

``qi.json`` owns the quantitative constants.  This module owns their executable
interpretation.  Combat, recovery, aging and any future Qi consumer import these
helpers rather than re-implementing the same formulas independently.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[3]
_QI_DATA = _ROOT / "game" / "data" / "martial-world" / "qi.json"


@lru_cache(maxsize=1)
def _mechanics() -> Mapping[str, Any]:
    data = json.loads(_QI_DATA.read_text(encoding="utf-8"))
    mechanics = data.get("mechanics") if isinstance(data, Mapping) else None
    if not isinstance(mechanics, Mapping):
        raise ValueError("jianghu qi mechanics invalid")
    return mechanics


def control_efficiency_milli(qi_control: int) -> int:
    c = max(0, int(qi_control))
    half = max(1, int(_mechanics().get("control_half_saturation", 100)))
    return c * 1000 // (c + half) if c else 0


def effective_cultivation_milli(qi: int, qi_control: int) -> int:
    q = max(0, int(qi)); c = max(0, int(qi_control))
    if q + c == 0:
        return 0
    multiplier = max(1, int(_mechanics().get("effective_cultivation_multiplier", 2)))
    return multiplier * q * c * 1000 // (q + c)


def effective_cultivation(qi: int, qi_control: int) -> float:
    return effective_cultivation_milli(qi, qi_control) / 1000.0


def biological_aging_rate(qi: int, qi_control: int) -> float:
    scale = max(1, int(_mechanics().get("aging_cultivation_scale", 100)))
    return 1.0 / math.sqrt(1.0 + effective_cultivation(qi, qi_control) / float(scale))


def current_qi_capacity_milli(qi: int) -> int:
    return max(0, int(qi)) * 1000


def qi_recovery_milli(
    *, qi: int, qi_control: int, current_qi_milli: int,
    elapsed_minutes: int, rest_state: str, health_milli: int = 1000,
    fatigue_milli: int = 0,
) -> dict[str, Any]:
    if elapsed_minutes < 0:
        raise ValueError("elapsed_minutes invalid")
    if rest_state not in {"combat", "strenuous", "travel", "awake_rest", "sleep"}:
        raise ValueError("rest_state invalid")
    cap = current_qi_capacity_milli(qi)
    current = max(0, min(cap, int(current_qi_milli)))
    if rest_state in {"combat", "strenuous"} or cap <= 0 or elapsed_minutes == 0:
        return {"recovered_milli": 0, "current_qi_milli_after": current, "capacity_milli": cap}
    recovery = _mechanics().get("recovery_per_hour_milli", {})
    if not isinstance(recovery, Mapping) or rest_state not in recovery:
        raise ValueError("jianghu qi recovery rule missing")
    base_per_hour_milli = max(0, int(recovery[rest_state]))
    efficiency = control_efficiency_milli(qi_control)
    minimum_control = max(0, min(1000, int(_mechanics().get("minimum_recovery_control_factor_milli", 500))))
    control_factor = minimum_control + efficiency * (1000 - minimum_control) // 1000
    health = max(0, min(1200, int(health_milli)))
    fatigue_factor = max(250, 1000 - max(0, int(fatigue_milli)) // 3)
    recovered = cap * base_per_hour_milli * elapsed_minutes * control_factor * health * fatigue_factor
    recovered //= 1000 * 60 * 1000 * 1000 * 1000
    recovered = max(0, min(cap - current, recovered))
    return {
        "recovered_milli": recovered,
        "current_qi_milli_after": current + recovered,
        "capacity_milli": cap,
        "control_efficiency_milli": efficiency,
    }



def person_current_qi_milli(person: Mapping[str, Any]) -> int:
    """Return exact current Qi without discarding fractional milli-Qi."""
    cap=current_qi_capacity_milli(int(person.get("qi",0)))
    if person.get("current_qi_milli") is not None:
        return max(0,min(cap,int(person.get("current_qi_milli",0))))
    return max(0,min(cap,int(person.get("current_qi",person.get("qi",0)))*1000))


def set_person_current_qi_milli(person: dict[str, Any], value_milli: int) -> int:
    cap=current_qi_capacity_milli(int(person.get("qi",0)))
    value=max(0,min(cap,int(value_milli)))
    person["current_qi_milli"]=value
    # Retain the coarse compatibility projection for old readers while milli-Qi
    # remains authoritative for new mechanics.
    person["current_qi"]=value//1000
    return value

def safe_flow_milli_per_second(qi: int, qi_control: int) -> int:
    q = max(0, int(qi)); eff = control_efficiency_milli(qi_control)
    return math.isqrt(q * 1000) * (1000 + eff) // 1000


def redistribution_latency_ms(qi_control: int) -> int:
    mechanics = _mechanics()
    base_ms = max(1, int(mechanics.get("redistribution_base_ms", 600)))
    scale = max(1, int(mechanics.get("redistribution_control_scale", 100)))
    minimum = max(1, int(mechanics.get("minimum_redistribution_latency_ms", 50)))
    return max(minimum, base_ms * scale // (scale + max(0, int(qi_control))))


def external_projection_decay_length_m(qi_control: int) -> float:
    mechanics = _mechanics()
    minimum_milli_m = max(1, int(mechanics.get("external_projection_min_decay_milli_m", 250)))
    scale = max(1, int(mechanics.get("external_projection_control_scale", 100)))
    return max(minimum_milli_m / 1000.0, max(0, int(qi_control)) / float(scale))


__all__ = [
    "biological_aging_rate", "control_efficiency_milli", "current_qi_capacity_milli",
    "effective_cultivation", "effective_cultivation_milli", "external_projection_decay_length_m",
    "person_current_qi_milli", "qi_recovery_milli", "redistribution_latency_ms", "safe_flow_milli_per_second",
    "set_person_current_qi_milli",
]
