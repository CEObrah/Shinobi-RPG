"""Finite melee-frontage policy for autonomous exact-combat team plans.

Exact combat already traces bodies, obstacles, weapon lanes and friendly bodies. The
team planner can still over-concentrate autonomous melee actors on one target before
those downstream checks run, especially with long-reach spears. This module treats the
space around a defender as finite melee frontage while preserving real multi-attacker
pressure from genuinely distinct directions.

A broad target-relative angular sector may contain one proactive autonomous melee
assignment. Extra proactive attackers must select another lawful target or wait for the
frontage to change. Generic tactical ``hold`` means guard the position without making a
fresh proactive attack. Player-retinue sector holds are different: those are delegated
formation-defense instructions and may still strike an enemy already inside the held
sector. The exact resolver keeps those two meanings separate at execution time.

Frontage is deterministic planning policy, not a second durable authority. Only the
ordinary registered assignment fields are changed. Current enemy plans are re-evaluated
from current geometry at each exact exchange so a stale plan cannot preserve a dogpile
after bodies move, while offensive assignments previously forced to wait can re-enter
when a lane actually opens.
"""
from __future__ import annotations

import copy
from contextvars import ContextVar
from typing import Any, Mapping, Sequence

from .geometry import facing_to_target_mdeg, planar_distance_mm
from . import team_tactics

_INSTALLED = False
_FRONTAGE_SECTOR_COUNT = 8
_RANGED_ROLES = frozenset({"ranged_denial", "shape", "track"})
_PASSIVE_HOLD_ROLES = frozenset({"anchor", "screen", "protect", "reserve", "medical_support"})
_CAPTURE_ROLES = frozenset({"control", "intercept", "exploit"})
_GENERIC_HOLD_ACTORS: ContextVar[frozenset[str]] = ContextVar(
    "shinobi_generic_hold_actors", default=frozenset()
)


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
    role = str(assignment.get("role") or "")
    # Passive planner roles are intentionally non-proactive. Offensive roles may
    # temporarily carry hold because an earlier frontage pass had no lane; those
    # rows must be reconsidered when geometry changes.
    if preferred == "hold" and role in _PASSIVE_HOLD_ROLES:
        return False
    if preferred in {"player_decides", "medical_support_hold"}:
        return False
    melee = max(_skill(record, "sword"), _skill(record, "spear"), _skill(record, "unarmed"))
    ranged = max(_skill(record, "bow"), _skill(record, "hidden_weapons"))
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


def _distance_band(distance_mm: int) -> int:
    distance = max(0, int(distance_mm))
    if distance <= 3_000:
        return 0
    if distance <= 8_000:
        return 1
    if distance <= 20_000:
        return 2
    return 3


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

    def key(ref: str) -> tuple[int, int, int, int, int, str]:
        target = positions[ref]
        distance = planar_distance_mm(attacker, target)
        return (
            -_distance_band(distance),
            1 if ref == original_target_ref else 0,
            1 if ref == primary_ref else 0,
            team_tactics.threat_score(records[ref]),
            -distance,
            ref,
        )

    return sorted(candidates, key=key, reverse=True)


def _reactivated_preference(plan: Mapping[str, Any], assignment: Mapping[str, Any]) -> str:
    preferred = str(assignment.get("preferred_action") or "attack")
    if preferred != "hold":
        return preferred
    role = str(assignment.get("role") or "")
    if role in _PASSIVE_HOLD_ROLES:
        return "hold"
    if str(plan.get("objective_kind") or "") == "capture" and role in _CAPTURE_ROLES:
        return "capture"
    return "attack"


def apply_frontage_to_plan(
    plan: Mapping[str, Any],
    *,
    known_enemy_refs: Sequence[str],
    records: Mapping[str, Mapping[str, Any]],
    positions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a plan whose proactive autonomous melee assignments respect frontage."""
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

        assignment["preferred_action"] = _reactivated_preference(out, assignment)
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
            assignment["preferred_action"] = "hold"
            adjusted[attacker_ref] = assignment
            continue

        occupied[chosen_ref].add(chosen_sector)
        assignment["target_ref"] = chosen_ref
        adjusted[attacker_ref] = assignment

    for key, value in assignments.items():
        if key not in adjusted:
            adjusted[key] = copy.deepcopy(value)
    out["assignments"] = adjusted
    return out


def _player_side(combat: Mapping[str, Any], player_ref: str) -> str | None:
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    for side_ref, members in sides.items():
        if isinstance(members, list) and player_ref in members:
            return str(side_ref)
    return None


def reapply_enemy_frontage(
    combat: Mapping[str, Any], *, people: Mapping[str, Mapping[str, Any]], player_ref: str
) -> dict[str, Any]:
    """Refresh autonomous enemy frontage from the current local geometry."""
    out = copy.deepcopy(dict(combat))
    team_plans = out.get("team_plans")
    positions = out.get("positions")
    if not isinstance(team_plans, dict) or not isinstance(positions, Mapping):
        return out
    player_side = _player_side(out, player_ref)
    for side_ref, raw_plan in list(team_plans.items()):
        if str(side_ref) == player_side or not isinstance(raw_plan, Mapping):
            continue
        known = raw_plan.get("known_enemy_refs", ())
        if not isinstance(known, (list, tuple)):
            continue
        team_plans[side_ref] = apply_frontage_to_plan(
            raw_plan,
            known_enemy_refs=[str(ref) for ref in known if isinstance(ref, str)],
            records=people,
            positions=positions,
        )
    return out


def _generic_hold_refs(
    combat: Mapping[str, Any], *, player_retinue_context: Mapping[str, Any] | None
) -> frozenset[str]:
    """Return plan-level holds that are not delegated player-retinue sector holds."""
    retinue_refs: set[str] = set()
    if isinstance(player_retinue_context, Mapping):
        for key in ("member_refs", "temporary_member_refs"):
            rows = player_retinue_context.get(key)
            if isinstance(rows, list):
                retinue_refs.update(str(ref) for ref in rows if isinstance(ref, str))
    plans = combat.get("team_plans", {}) if isinstance(combat.get("team_plans"), Mapping) else {}
    held: set[str] = set()
    for raw_plan in plans.values():
        assignments = raw_plan.get("assignments", {}) if isinstance(raw_plan, Mapping) else {}
        if not isinstance(assignments, Mapping):
            continue
        for actor_ref, raw_assignment in assignments.items():
            if not isinstance(actor_ref, str) or actor_ref in retinue_refs or not isinstance(raw_assignment, Mapping):
                continue
            if str(raw_assignment.get("preferred_action") or "") == "hold":
                held.add(actor_ref)
    return frozenset(held)


def install() -> None:
    """Install planner, live-frontage refresh, and true generic hold semantics."""
    global _INSTALLED
    if _INSTALLED:
        return
    from ..martial_world import exact_combat as exact

    original_plan = exact.plan_team_exchange
    if not getattr(original_plan, "_frontage_targeting", False):
        def plan_with_frontage(**kwargs: Any) -> dict[str, Any]:
            plan = original_plan(**kwargs)
            return apply_frontage_to_plan(
                plan,
                known_enemy_refs=kwargs.get("known_enemy_refs", ()),
                records=kwargs.get("records", {}),
                positions=kwargs.get("positions", {}),
            )

        plan_with_frontage._frontage_targeting = True  # type: ignore[attr-defined]
        exact.plan_team_exchange = plan_with_frontage
        if team_tactics.plan_team_exchange is original_plan:
            team_tactics.plan_team_exchange = plan_with_frontage

    original_hold_selector = exact._hold_position_weapon_for
    if not getattr(original_hold_selector, "_generic_hold_guard_only", False):
        def hold_position_weapon_for(
            person_ref: str,
            person: Mapping[str, Any],
            equipment_ledger: Mapping[str, Any],
            *,
            target_distance_mm: int,
        ):
            if person_ref in _GENERIC_HOLD_ACTORS.get():
                return None
            return original_hold_selector(
                person_ref,
                person,
                equipment_ledger,
                target_distance_mm=target_distance_mm,
            )

        hold_position_weapon_for._generic_hold_guard_only = True  # type: ignore[attr-defined]
        exact._hold_position_weapon_for = hold_position_weapon_for

    original_resolve = exact.resolve_exchange
    if not getattr(original_resolve, "_frontage_refresh", False):
        def resolve_with_frontage(**kwargs: Any) -> Mapping[str, Any]:
            combat = kwargs.get("combat")
            people = kwargs.get("people")
            player_ref = str(kwargs.get("player_ref") or "")
            adjusted = combat
            if isinstance(combat, Mapping) and isinstance(people, Mapping) and player_ref:
                adjusted = reapply_enemy_frontage(combat, people=people, player_ref=player_ref)
                if bool(kwargs.get("mutate_state")) and isinstance(combat, dict):
                    combat.clear()
                    combat.update(adjusted)
                    adjusted = combat
                kwargs["combat"] = adjusted
            token = _GENERIC_HOLD_ACTORS.set(
                _generic_hold_refs(
                    adjusted if isinstance(adjusted, Mapping) else {},
                    player_retinue_context=(
                        kwargs.get("player_retinue_context")
                        if isinstance(kwargs.get("player_retinue_context"), Mapping)
                        else None
                    ),
                )
            )
            try:
                return original_resolve(**kwargs)
            finally:
                _GENERIC_HOLD_ACTORS.reset(token)

        resolve_with_frontage._frontage_refresh = True  # type: ignore[attr-defined]
        exact.resolve_exchange = resolve_with_frontage

    _INSTALLED = True


__all__ = ["apply_frontage_to_plan", "install", "reapply_enemy_frontage"]
