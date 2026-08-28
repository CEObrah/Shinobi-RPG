"""Deterministic Jianghu riding-horse physics without a Riding skill.

The Jianghu world owns riding horses as conserved faction transport counts. Exact
combat may allocate one such horse to a rider for the duration of a local fight,
but never materializes a permanent horse person/entity merely to resolve combat.
Rider control is derived from existing attributes plus current bodily function.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .health import functional_capacity_factors

_ROOT = Path(__file__).resolve().parents[3]
_TRAVEL = _ROOT / "game" / "data" / "martial-world" / "travel.json"


def horse_profile() -> Mapping[str, Any]:
    data = json.loads(_TRAVEL.read_text(encoding="utf-8"))
    row = data.get("riding_horse_profile", {})
    if not isinstance(row, Mapping):
        raise ValueError("riding horse profile missing")
    return row


def _attrs(person: Mapping[str, Any]) -> Mapping[str, Any]:
    row = person.get("attributes")
    return row if isinstance(row, Mapping) else {}


def _wounds(person: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    health = person.get("health") if isinstance(person.get("health"), Mapping) else {}
    rows = health.get("injuries", []) if isinstance(health, Mapping) else []
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def rider_control_milli(person: Mapping[str, Any]) -> int:
    """Current horse-control expression from existing attributes and anatomy.

    This is deliberately not a skill. Dexterity handles fine body/rein control,
    Perception reads terrain/threats, Speed covers rapid whole-body adjustment,
    Endurance sustains posture, and Willpower covers composure under pressure.
    The anatomy layer then constrains what the rider can physically express.
    """
    attrs = _attrs(person)
    raw = (
        max(0, int(attrs.get("dexterity", 0))) * 35
        + max(0, int(attrs.get("perception", 0))) * 25
        + max(0, int(attrs.get("speed", 0))) * 20
        + max(0, int(attrs.get("endurance", 0))) * 10
        + max(0, int(attrs.get("willpower", 0))) * 10
    ) // 100
    # Ordinary trained adults cluster below perfect battlefield control while
    # exceptional existing attributes can continue to matter above 100.
    attribute_control = max(300, min(1450, 450 + raw * 6))
    body = functional_capacity_factors(_wounds(person))
    stability = max(0, min(1000, int(body.get("mounted_stability_milli", 1000))))
    return max(0, min(1450, attribute_control * stability // 1000))


def mounted_motion_profile(
    person: Mapping[str, Any],
    *,
    carried_mass_kg: float = 0.0,
    mount_state: Mapping[str, Any] | None = None,
    terrain_milli: int = 1000,
) -> dict[str, Any]:
    profile = horse_profile()
    state = mount_state if isinstance(mount_state, Mapping) else {}
    active = bool(state.get("active", True)) and str(state.get("status", "active")) == "active"
    if not active:
        return {
            "mounted": False,
            "control_milli": 0,
            "effective_speed_mmps": 0,
            "total_mass_kg": 0.0,
            "condition_milli": max(0, int(state.get("condition_milli", 0))),
        }

    control = rider_control_milli(person)
    condition = max(0, min(1000, int(state.get("condition_milli", 1000))))
    horse_mass = max(1.0, float(profile.get("mass_kg", 480.0)))
    rider_mass = max(20.0, float(person.get("body_mass_kg", 70.0)))
    comfortable = max(40.0, float(profile.get("comfortable_load_kg", 160.0)))
    load = rider_mass + max(0.0, float(carried_mass_kg))
    load_ratio = load / comfortable
    if load_ratio <= 0.80:
        load_milli = 1000
    elif load_ratio <= 1.00:
        load_milli = 1000 - int((load_ratio - 0.80) * 350)
    elif load_ratio <= 1.20:
        load_milli = 930 - int((load_ratio - 1.00) * 900)
    else:
        load_milli = max(450, 750 - int((load_ratio - 1.20) * 1000))
    control_speed_milli = max(550, min(1100, 600 + control * 400 // 1000))
    condition_speed_milli = max(350, condition)
    terrain = max(350, min(1100, int(terrain_milli)))
    base_speed = max(2500, int(profile.get("battlefield_speed_mmps", 9000)))
    effective = base_speed * load_milli // 1000
    effective = effective * control_speed_milli // 1000
    effective = effective * condition_speed_milli // 1000
    effective = effective * terrain // 1000
    return {
        "mounted": True,
        "control_milli": control,
        "effective_speed_mmps": max(800, effective),
        "base_speed_mmps": base_speed,
        "condition_milli": condition,
        "load_ratio_milli": max(0, int(round(load_ratio * 1000))),
        "load_factor_milli": load_milli,
        "terrain_milli": terrain,
        "horse_mass_kg": horse_mass,
        "total_mass_kg": horse_mass + load,
    }


def mount_contact_result(
    mount_state: Mapping[str, Any],
    *,
    cut: int,
    pierce: int,
    blunt: int,
    penetration: int,
) -> dict[str, Any]:
    """Apply one physical contact to a combat-local horse allocation.

    The exact horse is not a persistent world identity. Condition exists only
    while the horse is causally relevant in the local combat. A service-loss
    flag tells the command layer to debit the owning faction's conserved usable
    riding-horse count exactly once.
    """
    profile = horse_profile()
    before = max(0, min(1000, int(mount_state.get("condition_milli", 1000))))
    trauma = (
        max(0, int(cut)) * 3
        + max(0, int(pierce)) * 4
        + max(0, int(blunt)) * 2
        + max(0, int(penetration)) * 4
    )
    # Ordinary glancing contacts produce recoverable local burden; major spear,
    # arrow or heavy-cut contacts can remove a horse from service in one hit.
    damage = max(0, min(1000, trauma * 2 // 3))
    after = max(0, before - damage)
    disabled_threshold = max(1, int(profile.get("disabled_condition_milli", 300)))
    if after <= 0:
        status = "dead"
    elif after < disabled_threshold:
        status = "disabled"
    else:
        status = "active"
    service_loss = status in {"disabled", "dead"} and not bool(mount_state.get("inventory_debited", False))
    return {
        "condition_before_milli": before,
        "condition_after_milli": after,
        "damage_milli": damage,
        "status": status,
        "service_loss": service_loss,
        "channels": {
            "cut": max(0, int(cut)),
            "pierce": max(0, int(pierce)),
            "blunt": max(0, int(blunt)),
            "penetration": max(0, int(penetration)),
        },
    }


def active_mount_allocations(combats: Mapping[str, Any], *, faction_ref: str) -> int:
    total = 0
    rows = combats.get("combats", {}) if isinstance(combats.get("combats"), Mapping) else {}
    for combat in rows.values():
        if not isinstance(combat, Mapping) or combat.get("status") != "active":
            continue
        states = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
        for state in states.values():
            mount = state.get("mount") if isinstance(state, Mapping) else None
            if not isinstance(mount, Mapping):
                continue
            if str(mount.get("owner_faction_ref") or "") != faction_ref:
                continue
            if bool(mount.get("active", True)) and str(mount.get("status", "active")) == "active":
                total += 1
    return total


__all__ = [
    "active_mount_allocations",
    "horse_profile",
    "mount_contact_result",
    "mounted_motion_profile",
    "rider_control_milli",
]
