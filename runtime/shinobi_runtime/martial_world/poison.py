"""Finite deterministic fictional poison burden mechanics for Jianghu.

Poison is a conserved gameplay resource.  Resistance is physical/cultivation
based and has no artificial percentage cap.  A sufficiently weak exposure can
therefore establish no systemic burden at all.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game" / "data" / "martial-world"


def _data() -> dict[str, Any]:
    return json.loads((_MW / "poisons.json").read_text(encoding="utf-8"))


def poison_item_ref(poison_ref: str) -> str:
    if poison_ref not in _data().get("poisons", {}):
        raise KeyError(poison_ref)
    return f"poison_{poison_ref}"


def resistance_score(*, endurance: int, qi: int, qi_control: int) -> int:
    """Current deterministic resistance capacity, intentionally uncapped."""
    return max(0, int(endurance)) * 4 + max(0, int(qi)) * 3 + max(0, int(qi_control)) * 5


def exposure_pressure(*, poison_ref: str, doses: int) -> int:
    row = _data().get("poisons", {}).get(poison_ref)
    if not isinstance(row, dict):
        raise KeyError(poison_ref)
    if doses <= 0:
        raise ValueError("dose invalid")
    return max(1, int(row.get("potency", 0)) * 10 + max(1, int(doses)) * 180)


def apply_poison(
    *, poison_ref: str, current_burden: int, doses: int,
    endurance: int, qi: int, qi_control: int,
) -> dict[str, Any]:
    row = _data().get("poisons", {}).get(poison_ref)
    if not isinstance(row, dict):
        raise KeyError(poison_ref)
    if doses <= 0:
        raise ValueError("dose invalid")
    raw = max(0, int(row.get("burden_per_dose", 0))) * int(doses)
    resist = resistance_score(endurance=endurance, qi=qi, qi_control=qi_control)
    pressure = exposure_pressure(poison_ref=poison_ref, doses=doses)
    excess = max(0, pressure - resist)
    # No floor: complete deterministic rejection of weak exposure is lawful.
    added = 0 if excess <= 0 else max(1, raw * excess // pressure)
    before = max(0, int(current_burden))
    return {
        "poison_ref": poison_ref,
        "burden_before": before,
        "burden_after": before + added,
        "burden_added": added,
        "exposure_rejected": added == 0,
        "resistance_score": resist,
        "exposure_pressure": pressure,
        "onset_minutes": int(row.get("onset_minutes", 0)),
    }


def settle_poison(*, poison_ref: str, burden: int, elapsed_hours: int) -> dict[str, Any]:
    row = _data().get("poisons", {}).get(poison_ref)
    if not isinstance(row, dict):
        raise KeyError(poison_ref)
    clearance = max(0, int(_data().get("natural_clearance_per_hour", 0)))
    after = max(0, int(burden) - clearance * max(0, int(elapsed_hours)))
    effects: dict[str, int] = {}
    for key, value in row.get("effects_per_100_burden", {}).items():
        if isinstance(value, int) and not isinstance(value, bool):
            effects[str(key)] = value * after // 100
    return {"burden_after": after, "effects": effects}


def active_qi_purge(
    *, poison_ref: str, burden: int, current_qi: int, qi: int,
    qi_control: int, elapsed_minutes: int,
) -> dict[str, Any]:
    """Deliberately circulate Qi to clear established toxin burden.

    The rule is fictional game abstraction.  It contains no real-world toxin
    preparation or treatment instructions.
    """
    if poison_ref not in _data().get("poisons", {}):
        raise KeyError(poison_ref)
    before = max(0, int(burden))
    current = max(0, min(max(0, int(qi)), int(current_qi)))
    minutes = max(0, int(elapsed_minutes))
    if before <= 0 or current <= 0 or minutes <= 0:
        return {
            "poison_ref": poison_ref, "burden_before": before, "burden_after": before,
            "burden_cleared": 0, "current_qi_before": current, "current_qi_after": current,
            "qi_spent": 0, "elapsed_minutes": minutes,
        }
    cfg = _data().get("active_qi_purge", {})
    base_cost = max(1, int(cfg.get("qi_cost_per_burden", 4)))
    efficiency_milli = max(350, min(1800, 500 + max(0, int(qi_control)) * 6))
    # Better control reduces the Qi price but never makes purging free.
    qi_cost_milli_per_burden = max(500, base_cost * 1000 * 1000 // efficiency_milli)
    throughput_per_hour = max(1, max(0, int(qi_control)) // 6 + max(0, int(qi)) // 30)
    time_limited = throughput_per_hour * minutes // 60
    qi_limited = current * 1000 // qi_cost_milli_per_burden
    cleared = min(before, max(0, time_limited), max(0, qi_limited))
    spent_milli = cleared * qi_cost_milli_per_burden
    qi_spent = min(current, (spent_milli + 999) // 1000)
    return {
        "poison_ref": poison_ref,
        "burden_before": before,
        "burden_after": before - cleared,
        "burden_cleared": cleared,
        "current_qi_before": current,
        "current_qi_after": max(0, current - qi_spent),
        "qi_spent": qi_spent,
        "control_efficiency_milli": efficiency_milli,
        "elapsed_minutes": minutes,
    }


__all__ = [
    "active_qi_purge", "apply_poison", "exposure_pressure", "poison_item_ref",
    "resistance_score", "settle_poison",
]
