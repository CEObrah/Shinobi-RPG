"""Production safety policy for exact-combat command spans and adaptive attacks.

The exact resolver is deterministic and pure at the command-planning boundary,
so a standing-intent span may be re-evaluated with a smaller exchange frontier
before any transaction is committed. This module keeps long standing intents
bounded, returns control after protected player-decision casualties, preserves
rapid lethal-pursuit semantics, prevents a delegated ranged/thrown choice from
needlessly replacing available melee while the chosen target is already in
immediate close pressure, and prevents defensive reactions from being recorded
before the incoming attack has physically started.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Callable, Mapping

from shinobi_runtime.commands.combat_adaptation import adaptive_standing_span
from shinobi_runtime.martial_world.exact_combat import (
    currently_visible_enemies,
    default_weapon_for_action,
)


_LETHAL_PURSUIT_DOCTRINE = "doctrine.tang_wei.precision_function_denial.lethal_pursuit"
_MAX_STANDING_SPAN_ELAPSED_MS = 300_000
_STANDING_SPAN_EXCHANGE_FRONTIERS = (16, 8, 4, 2, 1)
_CLOSE_PRESSURE_DISTANCE_MM = 3_000
_TERMINAL_HEALTH = frozenset({"dead", "incapacitated"})
_TERMINAL_COMBAT_STATUS = frozenset({"dead", "unconscious", "incapacitated", "escaped", "reinforcing"})
_RANGED_ACTIONS = frozenset({"bow_shot", "hidden_weapon_throw"})
_MELEE_ACTIONS = frozenset({"cut", "thrust", "staff_strike", "staff_thrust", "staff_butt_strike", "staff_sweep"})


def _side_of(combat: Mapping[str, Any], actor_ref: str) -> str | None:
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    for side_ref, members in sides.items():
        if isinstance(members, list) and actor_ref in members:
            return str(side_ref)
    return None


def _rapid_lethal_candidates(
    *, combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], actor_ref: str
) -> list[str]:
    side = _side_of(combat, actor_ref)
    if side is None:
        return []
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    enemy_refs: list[str] = []
    for side_ref, members in sides.items():
        if str(side_ref) == side or not isinstance(members, list):
            continue
        enemy_refs.extend(str(ref) for ref in members if isinstance(ref, str))

    combatants = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    visible = set(currently_visible_enemies(
        combat, actor_ref=actor_ref, enemy_refs=enemy_refs, people=people,
    ))
    candidates: list[str] = []
    for ref in enemy_refs:
        if ref not in visible:
            continue
        person = people.get(ref)
        state = combatants.get(ref)
        if not isinstance(person, Mapping) or not isinstance(state, Mapping):
            continue
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        statuses = {str(value) for value in state.get("status_families", []) if isinstance(value, str)}
        if str(health.get("status") or "") in _TERMINAL_HEALTH:
            continue
        if int(health.get("consciousness", 100)) <= 0:
            continue
        if statuses & _TERMINAL_COMBAT_STATUS:
            continue
        candidates.append(ref)
    return candidates


def rapid_lethal_target_for(
    base_selector: Callable[..., str],
    *,
    combat: Mapping[str, Any],
    people: Mapping[str, Mapping[str, Any]],
    actor_ref: str,
    martial_familiarity: Mapping[str, Any] | None = None,
) -> str:
    """Select the nearest lawful active opponent during explicit rapid lethal pursuit.

    ``base_selector`` is called first so the exact combat observation machinery
    remains authoritative and can lawfully refresh encounter memory. The lethal
    override is then restricted to fresh current visibility; a remembered hidden
    opponent can never replace the lawful current target. All other doctrines
    retain the base selector exactly.
    """

    base_target = base_selector(
        combat=combat,
        people=people,
        actor_ref=actor_ref,
        martial_familiarity=martial_familiarity,
    )
    actor = people.get(actor_ref)
    if not isinstance(actor, Mapping) or str(actor.get("combat_doctrine_ref") or "") != _LETHAL_PURSUIT_DOCTRINE:
        return base_target

    candidates = _rapid_lethal_candidates(combat=combat, people=people, actor_ref=actor_ref)
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    actor_pos = positions.get(actor_ref)
    if not candidates or not isinstance(actor_pos, Mapping):
        return base_target

    actor_x = int(actor_pos.get("x_mm", 0))
    actor_y = int(actor_pos.get("y_mm", 0))

    def target_key(ref: str) -> tuple[int, str]:
        position = positions.get(ref)
        if not isinstance(position, Mapping):
            return (10**30, ref)
        dx = int(position.get("x_mm", 0)) - actor_x
        dy = int(position.get("y_mm", 0)) - actor_y
        return (dx * dx + dy * dy, ref)

    return min(candidates, key=target_key)


def close_pressure_action_for(
    base_selector: Callable[..., tuple[str, str]],
    *,
    melee_weapon_selector: Callable[..., str] = default_weapon_for_action,
    close_pressure_distance_mm: int = _CLOSE_PRESSURE_DISTANCE_MM,
    **kwargs: Any,
) -> tuple[str, str]:
    """Prefer usable melee for a delegated attack against an already-close target.

    This is deliberately narrow. Explicit player weapon choices are never
    changed. The base selector remains authoritative at normal range. Only when
    the delegated base choice is bow/thrown and the selected lawful target is
    already within immediate close pressure do we ask the existing explicit-
    technique weapon selector for a carried melee option, then route that option
    back through the same base selector so weapon legality and technique choice
    remain centralized.
    """
    base_choice = base_selector(**kwargs)
    preferred = kwargs.get("preferred_weapon_ref")
    if isinstance(preferred, str) and preferred not in {"", "auto"}:
        return base_choice
    if not isinstance(base_choice, tuple) or len(base_choice) != 2 or str(base_choice[0]) not in _RANGED_ACTIONS:
        return base_choice

    combat = kwargs.get("combat")
    actor_ref = str(kwargs.get("actor_ref") or "")
    target_ref = str(kwargs.get("target_ref") or "")
    people = kwargs.get("people")
    equipment_ledger = kwargs.get("equipment_ledger")
    if not isinstance(combat, Mapping) or not actor_ref or not target_ref:
        return base_choice
    if not isinstance(people, Mapping) or not isinstance(equipment_ledger, Mapping):
        return base_choice
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    actor_pos = positions.get(actor_ref)
    target_pos = positions.get(target_ref)
    if not isinstance(actor_pos, Mapping) or not isinstance(target_pos, Mapping):
        return base_choice
    if actor_pos.get("zone_ref") != target_pos.get("zone_ref"):
        return base_choice
    dx = int(target_pos.get("x_mm", 0)) - int(actor_pos.get("x_mm", 0))
    dy = int(target_pos.get("y_mm", 0)) - int(actor_pos.get("y_mm", 0))
    if math.isqrt(dx * dx + dy * dy) > max(1, int(close_pressure_distance_mm)):
        return base_choice

    candidates: list[str] = []
    combatants = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    actor_state = combatants.get(actor_ref)
    ready = actor_state.get("ready_weapon_ref") if isinstance(actor_state, Mapping) else None
    if isinstance(ready, str) and ready not in {"", "auto", "body_unarmed", str(base_choice[1])}:
        candidates.append(ready)
    for action_kind in ("cut", "thrust"):
        try:
            candidate = melee_weapon_selector(
                people=people,
                equipment_ledger=equipment_ledger,
                actor_ref=actor_ref,
                action_kind=action_kind,
            )
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(candidate, str) and candidate and candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        attempt = dict(kwargs)
        attempt["preferred_weapon_ref"] = candidate
        try:
            choice = base_selector(**attempt)
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(choice, tuple) and len(choice) == 2 and str(choice[0]) in _MELEE_ACTIONS:
            return str(choice[0]), str(choice[1])
    return base_choice


def pending_action_record_with_start(
    base_recorder: Callable[[Any], Mapping[str, Any]], action: Any
) -> dict[str, Any]:
    """Preserve the physical action-start frontier in transient pending state."""

    row = copy.deepcopy(dict(base_recorder(action)))
    row["start_at_ms"] = int(action.start_at_ms)
    return row


def physically_bounded_defensive_interruption(
    base_recorder: Callable[..., None],
    *,
    combat: dict[str, Any],
    defender_ref: str,
    attacker_ref: str,
    response: str,
    response_start_ms: int,
    response_contact_ms: int,
) -> None:
    """Never back-date a defense before the incoming attack physically starts.

    Exact combat estimates a response start by subtracting reaction latency from
    contact time. Under very fast defenders that estimate can precede the
    attacker's own scheduled start. Because defense-start time can cancel the
    defender's pending attack, that creates a retroactive/pre-cognitive offense
    starvation loop under multi-attacker pressure. Pending action records carry
    the attacker's real start frontier so the reaction can be bounded to causal
    time without changing contact, defense quality, or later interruption rules.
    """

    bounded_start = int(response_start_ms)
    pending = combat.get("_pending_actions", {}) if isinstance(combat.get("_pending_actions"), Mapping) else {}
    attacker_action = pending.get(attacker_ref) if isinstance(pending, Mapping) else None
    if isinstance(attacker_action, Mapping):
        raw_start = attacker_action.get("start_at_ms")
        if isinstance(raw_start, int) and not isinstance(raw_start, bool):
            bounded_start = max(bounded_start, int(raw_start))
    bounded_start = min(bounded_start, int(response_contact_ms))
    base_recorder(
        combat,
        defender_ref=defender_ref,
        attacker_ref=attacker_ref,
        response=response,
        response_start_ms=bounded_start,
        response_contact_ms=int(response_contact_ms),
    )


def _has_protected_player_decision(result: Mapping[str, Any]) -> bool:
    projection = result.get("narrative_projection")
    beats = projection.get("beats", []) if isinstance(projection, Mapping) else []
    return any(
        isinstance(row, Mapping) and bool(row.get("must_narrate_before_next_decision"))
        for row in beats
    )


def _mark_protected_stop(result: Mapping[str, Any]) -> Mapping[str, Any]:
    out = copy.deepcopy(dict(result))
    out["scope_stop_reason"] = "protected_player_decision"
    out["continuation_required"] = False
    projection = out.get("narrative_projection")
    if isinstance(projection, dict):
        projection["scope_stop_reason"] = "protected_player_decision"
    return out


def _truncate_at_protected_player_decision(
    base_resolver: Callable[..., Mapping[str, Any]],
    result: Mapping[str, Any],
    kwargs: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return the earliest deterministic exchange prefix containing a protected beat."""
    if not _has_protected_player_decision(result):
        return result
    exchanges = max(1, int(result.get("exchanges_resolved", 1)))
    if exchanges <= 1:
        return _mark_protected_stop(result)

    low = 1
    high = exchanges
    best: Mapping[str, Any] = result
    while low <= high:
        mid = (low + high) // 2
        attempt = dict(kwargs)
        attempt["frontier_exchanges"] = mid
        candidate = base_resolver(**attempt)
        if _has_protected_player_decision(candidate):
            best = candidate
            high = mid - 1
        else:
            low = mid + 1
    return _mark_protected_stop(best)


def _time_bounded_standing_result(
    base_resolver: Callable[..., Mapping[str, Any]],
    *,
    max_elapsed_ms: int,
    exchange_frontiers: tuple[int, ...],
    **kwargs: Any,
) -> Mapping[str, Any]:
    if not bool(kwargs.get("until_resolution")):
        return base_resolver(**kwargs)

    budget = max(1, int(max_elapsed_ms))
    combat = kwargs.get("combat")
    if not isinstance(combat, Mapping):
        return base_resolver(**kwargs)
    start_elapsed = max(0, int(combat.get("elapsed_ms", 0)))

    requested_frontier = kwargs.get("frontier_exchanges")
    max_exchanges = max(1, int(requested_frontier)) if requested_frontier is not None else None
    candidates = [
        max(1, int(value))
        for value in exchange_frontiers
        if int(value) > 0 and (max_exchanges is None or int(value) <= max_exchanges)
    ]
    if max_exchanges is not None and max_exchanges not in candidates and max_exchanges < max(exchange_frontiers, default=max_exchanges):
        candidates.insert(0, max_exchanges)
    candidates = sorted(set(candidates), reverse=True)
    if not candidates:
        candidates = [1]

    last_result: Mapping[str, Any] | None = None
    for frontier in candidates:
        attempt = dict(kwargs)
        attempt["frontier_exchanges"] = frontier
        result = base_resolver(**attempt)
        last_result = result
        combat_after = result.get("combat_after") if isinstance(result, Mapping) else None
        if not isinstance(combat_after, Mapping):
            return result
        elapsed = max(0, int(combat_after.get("elapsed_ms", 0)) - start_elapsed)
        if elapsed <= budget:
            return result

    if last_result is None:
        raise ValueError("standing combat span produced no bounded candidate")
    combat_after = last_result.get("combat_after") if isinstance(last_result, Mapping) else None
    elapsed = (
        max(0, int(combat_after.get("elapsed_ms", 0)) - start_elapsed)
        if isinstance(combat_after, Mapping)
        else budget + 1
    )
    if elapsed > budget:
        raise ValueError("single combat exchange exceeds standing-span execution time frontier")
    return last_result


def bounded_standing_span(
    base_resolver: Callable[..., Mapping[str, Any]],
    *,
    max_elapsed_ms: int = _MAX_STANDING_SPAN_ELAPSED_MS,
    exchange_frontiers: tuple[int, ...] = _STANDING_SPAN_EXCHANGE_FRONTIERS,
    **kwargs: Any,
) -> Mapping[str, Any]:
    """Bound time and stop before a later exchange crosses a player checkpoint.

    The base resolver works on detached copies. We may therefore re-evaluate a
    smaller deterministic prefix without committing any discarded suffix. This
    applies the existing execution-time budget to ``until_resolution`` intents
    and, for every multi-exchange mode, returns control immediately after the
    first exchange that generates a protected ``must_narrate_before_next_decision``
    beat.
    """
    result = _time_bounded_standing_result(
        base_resolver,
        max_elapsed_ms=max_elapsed_ms,
        exchange_frontiers=exchange_frontiers,
        **kwargs,
    )
    return _truncate_at_protected_player_decision(base_resolver, result, kwargs)


def install_production_combat_span_safety() -> None:
    """Install the campaign production policy once, without changing saved doctrine."""

    from shinobi_runtime.commands import jianghu_extended as extended
    from shinobi_runtime.martial_world import exact_combat as exact

    extended_installed = bool(getattr(extended, "_production_combat_span_safety_installed", False))
    timing_installed = bool(getattr(exact, "_production_defense_timing_safety_installed", False))

    if not timing_installed:
        base_pending_action_recorder = exact._pending_action_record
        base_defensive_interruption_recorder = exact._record_defensive_interruption

        def pending_action_recorder(action: Any) -> dict[str, Any]:
            return pending_action_record_with_start(base_pending_action_recorder, action)

        def defensive_interruption_recorder(combat: dict[str, Any], **kwargs: Any) -> None:
            physically_bounded_defensive_interruption(
                base_defensive_interruption_recorder,
                combat=combat,
                **kwargs,
            )

        exact._pending_action_record = pending_action_recorder
        exact._record_defensive_interruption = defensive_interruption_recorder
        exact._production_defense_timing_safety_installed = True

    if extended_installed:
        return

    base_target_selector = extended.default_target_for
    base_action_selector = extended.default_action_for
    base_resolver = extended._resolve_player_combat_span

    def target_selector(**kwargs: Any) -> str:
        return rapid_lethal_target_for(base_target_selector, **kwargs)

    def action_selector(**kwargs: Any) -> tuple[str, str]:
        return close_pressure_action_for(base_action_selector, **kwargs)

    def span_resolver(**kwargs: Any) -> Mapping[str, Any]:
        return adaptive_standing_span(
            base_resolver,
            fallback=bounded_standing_span,
            max_elapsed_ms=_MAX_STANDING_SPAN_ELAPSED_MS,
            standing_exchange_limit=max(_STANDING_SPAN_EXCHANGE_FRONTIERS),
            **kwargs,
        )

    extended.default_target_for = target_selector
    extended.default_action_for = action_selector
    extended._resolve_player_combat_span = span_resolver
    extended._production_combat_span_safety_installed = True


__all__ = [
    "bounded_standing_span",
    "close_pressure_action_for",
    "install_production_combat_span_safety",
    "pending_action_record_with_start",
    "physically_bounded_defensive_interruption",
    "rapid_lethal_target_for",
]
