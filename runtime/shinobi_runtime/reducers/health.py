"""Shared persistent health effects.

Combat, training, travel, disease, treatment, and future domain reducers should
change health through this module rather than maintaining separate injury
semantics.  The reducer knows only person-state structure and deterministic
personnel effects; it has no campaign-name or organization-specific logic.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


class HealthResolutionError(ValueError):
    """Raised when a person record cannot lawfully receive a health effect."""


def _health(record: Mapping[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any], list[Any]]:
    resources = record.get("resources")
    if not isinstance(resources, dict):
        raise HealthResolutionError("person resources are invalid")
    health = resources.get("health")
    if (
        not isinstance(health, dict)
        or isinstance(health.get("capacity"), bool)
        or not isinstance(health.get("capacity"), int)
        or isinstance(health.get("current"), bool)
        or not isinstance(health.get("current"), int)
    ):
        raise HealthResolutionError("person health resource is invalid")
    condition = record.get("condition")
    if not isinstance(condition, dict):
        raise HealthResolutionError("person condition is invalid")
    injuries = condition.get("injuries")
    if not isinstance(injuries, list):
        raise HealthResolutionError("person injuries are invalid")
    return health, condition, injuries


def apply_personnel_effect(
    record: Dict[str, Any],
    *,
    effect: Any,
    event_marker: str,
) -> None:
    """Apply one deterministic personnel consequence to an exact person.

    ``effect`` is intentionally structural: it needs ``after_resources`` and
    ``after_personnel`` attributes, matching combat personnel effects while
    remaining usable by other deterministic domains that produce the same
    consequence contract.
    """

    health, condition, injuries = _health(record)
    resources = record["resources"]
    for pool in effect.after_resources:
        saved = resources.get(pool.resource_ref)
        if isinstance(saved, dict) and isinstance(saved.get("current"), int):
            saved["current"] = pool.current

    after = effect.after_personnel
    capacity = max(1, health["capacity"])
    if after.killed:
        record["life_status"] = "dead"
        health["current"] = 0
        condition["readiness"] = "dead"
        marker = event_marker + ":fatal"
        if marker not in injuries:
            injuries.append(marker)
    elif after.incapacitated:
        health["current"] = min(health["current"], max(1, capacity // 3))
        condition["readiness"] = "incapacitated"
        marker = event_marker + ":incapacitated"
        if marker not in injuries:
            injuries.append(marker)
    elif after.wounded:
        health["current"] = min(health["current"], max(1, (capacity * 3) // 4))
        condition["readiness"] = "injured"
        marker = event_marker + ":wounded"
        if marker not in injuries:
            injuries.append(marker)
    elif after.captured:
        condition["readiness"] = "captured"
    elif after.escaped and condition.get("readiness") == "ready":
        condition["readiness"] = "ready"


def settle_recovery(
    record: Dict[str, Any],
    *,
    elapsed_seconds: int,
    policy: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Analytically settle routine rest/recovery over one elapsed interval.

    The function updates exact state only for the person being recovered. It
    never creates or removes permanent disability markers. Generic runtime
    wound/incapacitation markers clear only once health is fully restored and
    the configured minimum wound interval has elapsed.
    """
    if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, int) or elapsed_seconds <= 0:
        raise HealthResolutionError("recovery elapsed time must be positive")
    health, condition, injuries = _health(record)
    resources = record["resources"]
    hours = elapsed_seconds / 3600.0
    days = elapsed_seconds / 86400.0

    def rate(name: str) -> int:
        value = policy.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HealthResolutionError(f"invalid recovery policy field: {name}")
        return value

    health_rate = rate("health_per_day_milli_capacity")
    chakra_rate = rate("chakra_per_hour_milli_capacity")
    fatigue_rate = rate("fatigue_per_hour_milli_capacity")
    strain_rate = rate("strain_per_hour_milli_capacity")
    incap_multiplier = rate("incapacitated_health_multiplier_milli")
    min_clear = rate("minimum_wound_clear_hours")

    before = {
        "health": health.get("current"),
        "chakra": resources.get("chakra", {}).get("current") if isinstance(resources.get("chakra"), dict) else None,
        "fatigue": resources.get("fatigue", {}).get("current") if isinstance(resources.get("fatigue"), dict) else None,
        "strain": resources.get("strain", {}).get("current") if isinstance(resources.get("strain"), dict) else None,
        "readiness": condition.get("readiness"),
        "injuries": list(injuries),
    }

    effective_health_rate = health_rate
    if condition.get("readiness") == "incapacitated":
        effective_health_rate = (effective_health_rate * incap_multiplier) // 1000
    heal = int((health["capacity"] * effective_health_rate * days) // 1000)
    if heal > 0:
        health["current"] = min(health["capacity"], health["current"] + heal)

    chakra = resources.get("chakra")
    if isinstance(chakra, dict) and isinstance(chakra.get("current"), int) and isinstance(chakra.get("capacity"), int):
        gain = int((chakra["capacity"] * chakra_rate * hours) // 1000)
        chakra["current"] = min(chakra["capacity"], chakra["current"] + max(0, gain))

    fatigue = resources.get("fatigue")
    if isinstance(fatigue, dict) and isinstance(fatigue.get("current"), int) and isinstance(fatigue.get("capacity"), int):
        reduction = int((fatigue["capacity"] * fatigue_rate * hours) // 1000)
        fatigue["current"] = max(0, fatigue["current"] - max(0, reduction))

    strain = resources.get("strain")
    if isinstance(strain, dict) and isinstance(strain.get("current"), int):
        cap = strain.get("safe_capacity")
        if isinstance(cap, int):
            reduction = int((cap * strain_rate * hours) // 1000)
            strain["current"] = max(0, strain["current"] - max(0, reduction))

    if health["current"] >= health["capacity"]:
        if hours >= min_clear:
            injuries[:] = [
                marker
                for marker in injuries
                if not (
                    isinstance(marker, str)
                    and (marker.endswith(":wounded") or marker.endswith(":incapacitated"))
                )
            ]
        if condition.get("readiness") in ("injured", "limited", "incapacitated", "fatigued") and not injuries:
            condition["readiness"] = "ready"
        elif condition.get("readiness") == "incapacitated":
            condition["readiness"] = "injured"
    elif condition.get("readiness") == "incapacitated" and health["current"] >= max(1, health["capacity"] // 2):
        condition["readiness"] = "injured"

    after = {
        "health": health.get("current"),
        "chakra": resources.get("chakra", {}).get("current") if isinstance(resources.get("chakra"), dict) else None,
        "fatigue": resources.get("fatigue", {}).get("current") if isinstance(resources.get("fatigue"), dict) else None,
        "strain": resources.get("strain", {}).get("current") if isinstance(resources.get("strain"), dict) else None,
        "readiness": condition.get("readiness"),
        "injuries": list(injuries),
    }
    return {"elapsed_seconds": elapsed_seconds, "before": before, "after": after}
