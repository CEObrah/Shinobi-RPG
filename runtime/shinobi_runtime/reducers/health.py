"""Shared persistent health effects.

Combat, training, travel, disease, treatment, autonomous missions, and future
domain reducers should change health through this module rather than maintaining
separate injury semantics. The reducer accepts both full ``shinobi_character``
owners and compact persistent ``person`` owners without inventing a second
health authority for either representation.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


class HealthResolutionError(ValueError):
    """Raised when a person record cannot lawfully receive a health effect."""


def _health(
    record: Mapping[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any], list[Any], Dict[str, Any], str]:
    """Return one normalized health view over either exact-person representation."""

    if record.get("schema") == "person":
        stats = record.get("stats")
        if not isinstance(stats, dict):
            raise HealthResolutionError("person stats are invalid")
        resources = stats.get("resources")
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
        condition = record.get("health")
        if not isinstance(condition, dict):
            raise HealthResolutionError("person condition is invalid")
        status = condition.get("status")
        fatigue = condition.get("fatigue")
        if not isinstance(status, str) or not status:
            raise HealthResolutionError("person health status is invalid")
        if isinstance(fatigue, bool) or not isinstance(fatigue, int) or fatigue < 0:
            raise HealthResolutionError("person health fatigue is invalid")
        # Compact person owners intentionally keep injury detail in semantic
        # world events rather than growing a second injury ledger. Their health
        # status is the durable readiness marker.
        return health, condition, [], resources, "person"

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

    # Older shinobi-character records legitimately predate the explicit injury
    # ledger. Absence means "no recorded injuries" and is normalized on first
    # health-domain settlement. A present malformed value still fails closed.
    if "injuries" not in condition:
        condition["injuries"] = []
    injuries = condition.get("injuries")
    if not isinstance(injuries, list):
        raise HealthResolutionError("person injuries are invalid")
    return health, condition, injuries, resources, "shinobi_character"


def _readiness(condition: Mapping[str, Any], representation: str) -> str:
    if representation != "person":
        value = condition.get("readiness")
        return value if isinstance(value, str) else ""
    status = str(condition.get("status", "")).lower()
    if status in {"healthy", "ready", "fit", "active"}:
        return "ready"
    if status in {"wounded", "injured"}:
        return "injured"
    if status == "limited":
        return "limited"
    if status in {"incapacitated", "unconscious", "critical"}:
        return "incapacitated"
    if status in {"dead", "deceased"}:
        return "dead"
    if status == "captured":
        return "captured"
    if status == "fatigued":
        return "fatigued"
    return status


def _set_readiness(condition: Dict[str, Any], representation: str, value: str) -> None:
    if representation != "person":
        condition["readiness"] = value
        return
    mapped = {
        "ready": "healthy",
        "injured": "wounded",
        "limited": "limited",
        "incapacitated": "incapacitated",
        "dead": "dead",
        "captured": "captured",
        "fatigued": "fatigued",
    }.get(value, value)
    condition["status"] = mapped


def _sync_compact_health(
    condition: Dict[str, Any], resources: Mapping[str, Any], representation: str
) -> None:
    if representation != "person":
        return
    fatigue = resources.get("fatigue")
    if isinstance(fatigue, Mapping):
        current = fatigue.get("current")
        if isinstance(current, int) and not isinstance(current, bool):
            condition["fatigue"] = current


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

    health, condition, injuries, resources, representation = _health(record)
    for pool in effect.after_resources:
        saved = resources.get(pool.resource_ref)
        if isinstance(saved, dict) and isinstance(saved.get("current"), int):
            saved["current"] = pool.current

    after = effect.after_personnel
    capacity = max(1, health["capacity"])
    if after.killed:
        if representation != "person":
            record["life_status"] = "dead"
        health["current"] = 0
        _set_readiness(condition, representation, "dead")
        if representation != "person":
            marker = event_marker + ":fatal"
            if marker not in injuries:
                injuries.append(marker)
    elif after.incapacitated:
        health["current"] = min(health["current"], max(1, capacity // 3))
        _set_readiness(condition, representation, "incapacitated")
        if representation != "person":
            marker = event_marker + ":incapacitated"
            if marker not in injuries:
                injuries.append(marker)
    elif after.wounded:
        health["current"] = min(health["current"], max(1, (capacity * 3) // 4))
        _set_readiness(condition, representation, "injured")
        if representation != "person":
            marker = event_marker + ":wounded"
            if marker not in injuries:
                injuries.append(marker)
    elif after.captured:
        _set_readiness(condition, representation, "captured")
    elif after.escaped and _readiness(condition, representation) == "ready":
        _set_readiness(condition, representation, "ready")
    _sync_compact_health(condition, resources, representation)


def settle_recovery(
    record: Dict[str, Any],
    *,
    elapsed_seconds: int,
    policy: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Analytically settle routine rest/recovery over one elapsed interval.

    The function updates exact state only for the person being recovered. It
    never creates or removes permanent disability markers. Full characters use
    their explicit injury ledger; compact ``person`` owners use their health
    status as the durable readiness marker and semantic events as injury history.
    """
    if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, int) or elapsed_seconds <= 0:
        raise HealthResolutionError("recovery elapsed time must be positive")
    health, condition, injuries, resources, representation = _health(record)
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
        "readiness": _readiness(condition, representation),
        "injuries": list(injuries),
    }

    readiness = _readiness(condition, representation)
    effective_health_rate = health_rate
    if readiness == "incapacitated":
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

    readiness = _readiness(condition, representation)
    if health["current"] >= health["capacity"]:
        if representation == "person":
            if readiness == "incapacitated" and hours < min_clear:
                _set_readiness(condition, representation, "injured")
            elif readiness in {"injured", "limited", "incapacitated", "fatigued"} and hours >= min_clear:
                _set_readiness(condition, representation, "ready")
        else:
            if hours >= min_clear:
                injuries[:] = [
                    marker
                    for marker in injuries
                    if not (
                        isinstance(marker, str)
                        and (marker.endswith(":wounded") or marker.endswith(":incapacitated"))
                    )
                ]
            readiness = _readiness(condition, representation)
            if readiness in ("injured", "limited", "incapacitated", "fatigued") and not injuries:
                _set_readiness(condition, representation, "ready")
            elif readiness == "incapacitated":
                _set_readiness(condition, representation, "injured")
    elif readiness == "incapacitated" and health["current"] >= max(1, health["capacity"] // 2):
        _set_readiness(condition, representation, "injured")

    _sync_compact_health(condition, resources, representation)
    after = {
        "health": health.get("current"),
        "chakra": resources.get("chakra", {}).get("current") if isinstance(resources.get("chakra"), dict) else None,
        "fatigue": resources.get("fatigue", {}).get("current") if isinstance(resources.get("fatigue"), dict) else None,
        "strain": resources.get("strain", {}).get("current") if isinstance(resources.get("strain"), dict) else None,
        "readiness": _readiness(condition, representation),
        "injuries": list(injuries),
    }
    return {"elapsed_seconds": elapsed_seconds, "before": before, "after": after}
