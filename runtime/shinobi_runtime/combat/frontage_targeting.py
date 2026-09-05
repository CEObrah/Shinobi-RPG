"""Target-frontage correction for autonomous exact-combat team plans.

Physical exact combat already traces bodies, obstacles, and friendly attack lanes.
The tactical planner can nevertheless over-concentrate a whole team on one primary
opponent before those downstream collision checks run. Long-reach melee weapons make
that planning defect especially visible because more attackers begin inside nominal
weapon reach.

This module keeps reach physical and keeps multi-attacker pressure, but treats the
space around a defender as finite melee frontage. One autonomous melee assignment may
occupy each broad target-relative angular sector in a plan. Additional attackers must
use a different lawful target or hold until geometry changes. A genuinely surrounded
defender can therefore still be pressured from several distinct directions; this is
not a global attacker-count cap.

Frontage is deterministic planning policy, not durable state of its own. The returned
plan therefore persists only the ordinary assignment consequences (target selection
and hold/attack posture) already registered by the combat-state contract. Sector
numbers and policy diagnostics remain local to this planning pass so adding or tuning
the algorithm cannot silently expand mutable-state schema.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from .geometry import facing_to_target_mdeg, planar_distance_mm
from . import team_tactics

_INSTALLED = False
_FRONTAGE_SECTOR_COUNT = 8
_RANGED_ROLES = frozenset({"ranged_denial", "shape", "track"})


def _skill(record: Mapping[str, Any], key: str) -> int:
    best = 0
    for container_key in (None, "martial_skills", "attributes"):
        container = record if container_key is None else record.get(container_key, {})
        if not isinstance(container, Mapping):
            continue
        raw = container.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool):
            best = max(best, raw)
    return max(0, best)


def _uses_melee_frontage(assignment: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    preferred = str(assignment.get("preferred_action") or "attack")
    if preferred == "hold":
        return False
    role = str(assignment.get("role") or "")
    melee = max(_skill(record, "sword"), _skill(record, "spear"), _skill(record, "unarmed"))
    ranged = max(_skill(record, "bow"), _skill(record, "hidden_weapons"))
    # Roles explicitly intended to work at range should not consume melee frontage
    # when the member is actually a stronger ranged fighter. Everyone else who is
    # being told to attack/capture competes for physical close-combat access.
    return not (role in _RANGED_ROLES and ranged > melee)


def _frontage_sector(
    positions: Mapping[str, Mapping[str, Any]], *, attacker_ref: str, target_ref: str
) -> int | None:
    attacker = positions.get(attacker_ref)
    target = positions.get(target_ref)
    if not isinstance(attacker, Mapping) or not isinstance(target, Mapping):
        return None
    if attacker.get("zone_ref") != target.get("zone_ref"):
        return None
    bearing = facing_to_target_mdeg(target, attacker)
    return (bearing * _FRONTAGE_SECTOR_COUNT // 360_000) % _FRONTAGE_SECTOR_COUNT


def _ordered_targets(
    *,
    attacker_ref: str,
    original_target_ref: str | None,
    primary_ref: str | None,
    enemy_refs: Sequence[str],
    records: Mapping[str, Mapping[str, Any]],
    positions: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    attacker = positions.get(attacker_ref)
    if not isinstance(attacker, Mapping):
        return []
    candidates: list[str] = []
    for ref in dict.fromkeys(enemy_refs):
        target = positions.get(ref)
        if ref not in records or not isinstance(target, Mapping):
            continue
        if target.get("zone_ref") != attacker.get("zone_ref"):
            continue
        candidates.append(ref)

    def key(ref: str) -> tuple[int, int, int, int, str]:
        target = positions[ref]
        distance = planar_distance_mm(attacker, target)
        return (
            1 if ref == original_target_ref else 0,
            1 if ref == primary_ref else 0,
            team_tactics.threat_score(records[ref]),
            -distance,
            ref,
        )

    return sorted(candidates, key=key, reverse=True)


def apply_frontage_to_plan(
    plan: Mapping[str, Any],
    *,
    known_enemy_refs: Sequence[str],
    records: Mapping[str, Mapping[str, Any]],
    positions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a plan whose autonomous melee assignments respect target frontage.

    The policy is realized by changing only the assignment fields the ordinary
    team-plan schema already owns: ``target_ref`` and ``preferred_action``.
    Recomputable sector/debug annotations deliberately do not escape this layer.
    """
    out = copy.deepcopy(dict(plan))
    assignments = out.get("assignments")
    if not isinstance(assignments, Mapping) or not assignments:
        return out
    primary_raw = out.get("primary_threat_ref")
    primary_ref = primary_raw if isinstance(primary_raw, str) else None
    enemies = [ref for ref in dict.fromkeys(known_enemy_refs) if isinstance(ref, str) and ref in records]
    if not enemies:
        return out

    occupied: dict[str, set[int]] = {ref: set() for ref in enemies}
    adjusted: dict[str, dict[str, Any]] = {}
    for attacker_ref, raw_assignment in assignments.items():
        if not isinstance(attacker_ref, str) or not isinstance(raw_assignment, Mapping):
            continue
        assignment = copy.deepcopy(dict(raw_assignment))
        record = records.get(attacker_ref)
        if not isinstance(record, Mapping) or not _uses_melee_frontage(assignment, record):
            adjusted[attacker_ref] = assignment
            continue

        original_raw = assignment.get("target_ref")
        original_target = original_raw if isinstance(original_raw, str) else None
        chosen_ref: str | None = None
        chosen_sector: int | None = None
        for target_ref in _ordered_targets(
            attacker_ref=attacker_ref,
            original_target_ref=original_target,
            primary_ref=primary_ref,
            enemy_refs=enemies,
            records=records,
            positions=positions,
        ):
            sector = _frontage_sector(positions, attacker_ref=attacker_ref, target_ref=target_ref)
            if sector is None or sector in occupied.setdefault(target_ref, set()):
                continue
            chosen_ref = target_ref
            chosen_sector = sector
            break

        if chosen_ref is None or chosen_sector is None:
            # No distinct physical attack sector is presently available. Holding
            # preserves pressure without pretending another body/weapon lane exists.
            assignment["preferred_action"] = "hold"
            adjusted[attacker_ref] = assignment
            continue

        occupied[chosen_ref].add(chosen_sector)
        assignment["target_ref"] = chosen_ref
        adjusted[attacker_ref] = assignment

    # Preserve any unusual non-string assignment keys rather than silently dropping
    # them; normal planner output is string-keyed, but this layer should be conservative.
    for key, value in assignments.items():
        if key not in adjusted:
            adjusted[key] = copy.deepcopy(value)
    out["assignments"] = adjusted
    return out


def install() -> None:
    """Install frontage correction on both public and exact-combat planner references."""
    global _INSTALLED
    if _INSTALLED:
        return
    from ..martial_world import exact_combat as exact

    original = exact.plan_team_exchange
    if getattr(original, "_frontage_targeting", False):
        _INSTALLED = True
        return

    def plan_with_frontage(**kwargs: Any) -> dict[str, Any]:
        plan = original(**kwargs)
        return apply_frontage_to_plan(
            plan,
            known_enemy_refs=kwargs.get("known_enemy_refs", ()),
            records=kwargs.get("records", {}),
            positions=kwargs.get("positions", {}),
        )

    plan_with_frontage._frontage_targeting = True  # type: ignore[attr-defined]
    exact.plan_team_exchange = plan_with_frontage
    if team_tactics.plan_team_exchange is original:
        team_tactics.plan_team_exchange = plan_with_frontage
    _INSTALLED = True


__all__ = ["apply_frontage_to_plan", "install"]
