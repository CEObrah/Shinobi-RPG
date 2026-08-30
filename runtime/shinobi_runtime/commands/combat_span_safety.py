"""Production safety policy for long exact-combat command spans.

The exact resolver is deterministic and pure at the command-planning boundary,
so a standing-intent span may be re-evaluated with a smaller exchange frontier
before any transaction is committed.  This module uses that property to keep a
single gameplay write from consuming hours of simulated time while preserving
ordinary one-exchange mechanics unchanged.

It also preserves the player's explicit "kill as many as possible as quickly as
possible" semantics.  The temporary lethal-pursuit doctrine is a command-span
marker; while it is active, autonomous target choice favors the nearest lawful
active opponent instead of generic hostility/team pressure that can otherwise
send Wei after a distant retreating target.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping


_LETHAL_PURSUIT_DOCTRINE = "doctrine.tang_wei.precision_function_denial.lethal_pursuit"
_MAX_STANDING_SPAN_ELAPSED_MS = 300_000
_STANDING_SPAN_EXCHANGE_FRONTIERS = (16, 8, 4, 2, 1)
_TERMINAL_HEALTH = frozenset({"dead", "incapacitated"})
_TERMINAL_COMBAT_STATUS = frozenset({"dead", "unconscious", "incapacitated", "escaped", "reinforcing"})


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
    actor_state = combatants.get(actor_ref, {}) if isinstance(combatants.get(actor_ref), Mapping) else {}
    observed = {str(ref) for ref in actor_state.get("observed_refs", []) if isinstance(ref, str)}
    candidates: list[str] = []
    for ref in enemy_refs:
        if ref not in observed:
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
    remains authoritative and can lawfully refresh ``observed_refs``.  All other
    doctrines retain the base selector exactly.
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


def bounded_standing_span(
    base_resolver: Callable[..., Mapping[str, Any]],
    *,
    max_elapsed_ms: int = _MAX_STANDING_SPAN_ELAPSED_MS,
    exchange_frontiers: tuple[int, ...] = _STANDING_SPAN_EXCHANGE_FRONTIERS,
    **kwargs: Any,
) -> Mapping[str, Any]:
    """Return the largest deterministic standing-intent chunk within the time budget.

    The base resolver works on deep copies, so retries here are read-only planning
    work.  The selected result is the only value that can reach transaction
    execution.  Explicit finite exchange/duration scopes retain the base resolver
    unchanged; this safety envelope is for ``until_resolution`` standing intent.
    """

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


def install_production_combat_span_safety() -> None:
    """Install the campaign production policy once, without changing saved doctrine."""

    from shinobi_runtime.commands import jianghu_extended as extended

    if bool(getattr(extended, "_production_combat_span_safety_installed", False)):
        return

    base_selector = extended.default_target_for
    base_resolver = extended._resolve_player_combat_span

    def target_selector(**kwargs: Any) -> str:
        return rapid_lethal_target_for(base_selector, **kwargs)

    def span_resolver(**kwargs: Any) -> Mapping[str, Any]:
        return bounded_standing_span(base_resolver, **kwargs)

    extended.default_target_for = target_selector
    extended._resolve_player_combat_span = span_resolver
    extended._production_combat_span_safety_installed = True


__all__ = [
    "bounded_standing_span",
    "install_production_combat_span_safety",
    "rapid_lethal_target_for",
]
