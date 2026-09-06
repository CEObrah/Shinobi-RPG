"""Span-local tactical adaptation for delegated player combat.

The exact combat resolver remains the sole consequence authority. This module
only decides which omitted tactical details to delegate on the next exchange,
using current lawful visibility plus player-safe results from earlier exchanges
inside the same already-authorized combat span.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

from shinobi_runtime.martial_world.doctrines import resolve_individual_doctrine
from shinobi_runtime.martial_world.exact_combat import currently_visible_enemies
from shinobi_runtime.martial_world.qi import person_current_qi_milli, safe_flow_milli_per_second
from shinobi_runtime.martial_world.social_causality import apply_martial_events, breach_hostile_commitments


_AUTO_TARGETS = frozenset({"", "auto"})
_AUTO_ACTIONS = frozenset({"attack", "auto"})
_AUTO_WEAPONS = frozenset({"", "auto"})
_TERMINAL_HEALTH = frozenset({"dead", "incapacitated"})
_TERMINAL_COMBAT_STATUS = frozenset({"dead", "unconscious", "incapacitated", "escaped", "reinforcing"})
_REJECTED_RESULTS = frozenset({
    "invalid_target", "friendly_target_rejected", "target_unavailable", "action_rejected",
    "target_not_observed", "no_lawfully_known_target", "strength_draw_requirement_not_met",
    "melee_approach_blocked", "mount_target_unavailable",
})
_DEFINITE_FAILURE_RESULTS = _REJECTED_RESULTS | frozenset({
    "defended_or_missed", "missed", "blocked", "parried", "dodged",
    "miss_no_spatial_intersection", "target_outpaced_committed_approach",
    "attack_interrupted", "interrupted_before_contact",
})
_ACTION_ALTERNATES = {
    "thrust": "cut",
    "cut": "thrust",
    "staff_strike": "staff_thrust",
    "staff_thrust": "staff_strike",
}
_MELEE_ACTIONS = frozenset({
    "cut", "thrust", "unarmed_strike", "staff_strike", "staff_sweep",
    "staff_thrust", "staff_butt_strike", "improvised_strike",
})
_PROJECTILE_ACTIONS = frozenset({"bow_shot", "hidden_weapon_throw"})
_ADAPTIVE_ZONES = ("chest", "forearm", "knee")
_ADAPTIVE_MOVEMENT_SENTINEL = "_adaptive_movement_intent"


def intelligence_adaptation_threshold(people: Mapping[str, Mapping[str, Any]], actor_ref: str) -> int:
    """Return deterministic failure repetitions tolerated before replanning."""
    actor = people.get(actor_ref)
    attributes = actor.get("attributes", {}) if isinstance(actor, Mapping) and isinstance(actor.get("attributes"), Mapping) else {}
    intelligence = max(0, min(100, int(attributes.get("intelligence", 50) or 0)))
    if intelligence >= 90:
        return 1
    if intelligence >= 70:
        return 2
    if intelligence >= 50:
        return 3
    return 4


def _side_of(combat: Mapping[str, Any], ref: str) -> str | None:
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    for side_ref, members in sides.items():
        if isinstance(members, list) and ref in members:
            return str(side_ref)
    return None


def _active_person(person: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    statuses = {str(value) for value in state.get("status_families", []) if isinstance(value, str)}
    if str(health.get("status") or "") in _TERMINAL_HEALTH:
        return False
    if int(health.get("consciousness", 100) or 0) <= 0:
        return False
    return not bool(statuses & _TERMINAL_COMBAT_STATUS)


def _enemy_refs(combat: Mapping[str, Any], actor_ref: str) -> list[str]:
    actor_side = _side_of(combat, actor_ref)
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    return [
        str(ref)
        for side_ref, members in sides.items()
        if str(side_ref) != actor_side and isinstance(members, list)
        for ref in members if isinstance(ref, str)
    ]


def visible_active_enemies(
    combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], actor_ref: str
) -> list[str]:
    """Return only currently visible, active opponents in deterministic distance order."""
    enemies = _enemy_refs(combat, actor_ref)
    try:
        visible = set(currently_visible_enemies(
            combat, actor_ref=actor_ref, enemy_refs=enemies, people=people,
        ))
    except (KeyError, TypeError, ValueError):
        return []
    combatants = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    actor_pos = positions.get(actor_ref)

    candidates: list[str] = []
    for ref in enemies:
        person = people.get(ref)
        state = combatants.get(ref)
        if ref not in visible or not isinstance(person, Mapping) or not isinstance(state, Mapping):
            continue
        if _active_person(person, state):
            candidates.append(ref)

    def key(ref: str) -> tuple[int, str]:
        target_pos = positions.get(ref)
        if not isinstance(actor_pos, Mapping) or not isinstance(target_pos, Mapping):
            return (10**30, ref)
        dx = int(target_pos.get("x_mm", 0)) - int(actor_pos.get("x_mm", 0))
        dy = int(target_pos.get("y_mm", 0)) - int(actor_pos.get("y_mm", 0))
        return (dx * dx + dy * dy, ref)

    return sorted(candidates, key=key)


def _injury_severity(injury: Mapping[str, Any]) -> int:
    for key in ("severity", "severity_level", "grade"):
        value = injury.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
        if isinstance(value, str):
            return {"minor": 1, "moderate": 2, "severe": 3, "critical": 4}.get(value.lower(), 0)
    return 0


def _vulnerable_allies(
    combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], actor_ref: str
) -> set[str]:
    side = _side_of(combat, actor_ref)
    if side is None:
        return set()
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    allies = sides.get(side, []) if isinstance(sides.get(side), list) else []
    vulnerable: set[str] = set()
    for ref in allies:
        if not isinstance(ref, str) or ref == actor_ref:
            continue
        person = people.get(ref)
        if not isinstance(person, Mapping):
            continue
        health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
        injuries = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
        if (
            int(health.get("consciousness", 100) or 0) < 85
            or int(health.get("shock", 0) or 0) >= 80
            or any(isinstance(row, Mapping) and _injury_severity(row) >= 2 for row in injuries)
        ):
            vulnerable.add(ref)
    return vulnerable


def _player_attack_event(events: Sequence[Mapping[str, Any]], actor_ref: str) -> Mapping[str, Any] | None:
    rows = [
        row for row in events
        if isinstance(row, Mapping)
        and str(row.get("actor_ref") or "") == actor_ref
        and isinstance(row.get("action_kind"), str)
        and str(row.get("action_kind") or "") not in {"ally_support", "rally"}
    ]
    return rows[-1] if rows else None


def definite_tactical_failure(event: Mapping[str, Any] | None) -> bool:
    if not isinstance(event, Mapping):
        return False
    return str(event.get("result") or "") in _DEFINITE_FAILURE_RESULTS


def _pressure_targets_from_projection(
    projection: Mapping[str, Any] | None,
    *, combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], actor_ref: str,
) -> list[str]:
    if not isinstance(projection, Mapping):
        return []
    vulnerable = _vulnerable_allies(combat, people, actor_ref)
    if not vulnerable:
        return []
    visible = set(visible_active_enemies(combat, people, actor_ref))
    beats = projection.get("beats", []) if isinstance(projection.get("beats"), list) else []
    ordered: list[str] = []
    for beat in reversed(beats):
        if not isinstance(beat, Mapping):
            continue
        enemy_ref = str(beat.get("actor_ref") or "")
        ally_ref = str(beat.get("target_ref") or "")
        if enemy_ref in visible and ally_ref in vulnerable and enemy_ref not in ordered:
            ordered.append(enemy_ref)
    return ordered


def adaptive_override_candidates(
    *,
    original_kwargs: Mapping[str, Any],
    combat: Mapping[str, Any],
    people: Mapping[str, Mapping[str, Any]],
    player_ref: str,
    previous_event: Mapping[str, Any] | None,
    previous_projection: Mapping[str, Any] | None,
    movement_already_used: bool = False,
) -> list[dict[str, Any]]:
    """Return lawful omitted-detail variations, ordered by tactical value.

    Local geometry and the fighter's already-selected discipline are exhausted
    before a generic target switch. This avoids treating a single failed sword
    entry as a reason to jump immediately to a weaker ranged discipline.
    """
    candidates: list[dict[str, Any]] = []
    previous_target = str(previous_event.get("intended_ref") or "") if isinstance(previous_event, Mapping) else ""
    previous_action = str(previous_event.get("action_kind") or "") if isinstance(previous_event, Mapping) else ""
    previous_zone = str(previous_event.get("hit_zone") or "") if isinstance(previous_event, Mapping) else ""

    target_delegated = str(original_kwargs.get("raw_target_ref") or "auto") in _AUTO_TARGETS
    action_delegated = str(original_kwargs.get("raw_action_kind") or "attack") in _AUTO_ACTIONS
    weapon_delegated = str(original_kwargs.get("raw_weapon_ref") or "auto") in _AUTO_WEAPONS
    hit_zone_delegated = str(original_kwargs.get("hit_zone") or "auto") in _AUTO_TARGETS
    structure_delegated = original_kwargs.get("target_structure_ref") in (None, "", "auto")

    if (
        not movement_already_used
        and previous_action in _MELEE_ACTIONS
        and (target_delegated or action_delegated or weapon_delegated)
    ):
        candidates.append({_ADAPTIVE_MOVEMENT_SENTINEL: "lateral"})

    if action_delegated:
        alternate = _ACTION_ALTERNATES.get(previous_action)
        if alternate:
            candidates.append({"raw_action_kind": alternate})

    if hit_zone_delegated and structure_delegated:
        for zone in _ADAPTIVE_ZONES:
            if zone != previous_zone:
                candidates.append({"hit_zone": zone})

    visible = visible_active_enemies(combat, people, player_ref)
    if target_delegated:
        for ref in _pressure_targets_from_projection(
            previous_projection, combat=combat, people=people, actor_ref=player_ref,
        ):
            if ref != previous_target:
                candidates.append({"raw_target_ref": ref})

        for ref in visible:
            candidate = {"raw_target_ref": ref}
            if ref != previous_target and candidate not in candidates:
                candidates.append(candidate)

    return candidates


def _explicit_target_available(kwargs: Mapping[str, Any]) -> bool:
    target_ref = str(kwargs.get("raw_target_ref") or "auto")
    if target_ref in _AUTO_TARGETS:
        return True
    combat = kwargs.get("combat")
    people = kwargs.get("people")
    if not isinstance(combat, Mapping) or not isinstance(people, Mapping):
        return False
    target = people.get(target_ref)
    combatants = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    state = combatants.get(target_ref)
    if not isinstance(target, Mapping) or not isinstance(state, Mapping) or not _active_person(target, state):
        return False
    if str(kwargs.get("hit_zone") or "") == "mount":
        mount = state.get("mount") if isinstance(state.get("mount"), Mapping) else None
        if not isinstance(mount, Mapping) or not bool(mount.get("active", True)) or str(mount.get("status") or "active") != "active":
            return False
    return True


def _update_social_cursor(
    social_cursor: Mapping[str, Any], events: Sequence[Mapping[str, Any]], *,
    combat: Mapping[str, Any], people: Mapping[str, Mapping[str, Any]], player_ref: str,
) -> dict[str, Any]:
    side_by_ref = {
        str(ref): str(side)
        for side, refs in (combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}).items()
        if isinstance(refs, list) for ref in refs if isinstance(ref, str)
    }
    out = apply_martial_events(copy.deepcopy(dict(social_cursor)), events, side_by_ref=side_by_ref)
    for event in events:
        if str(event.get("actor_ref") or "") != player_ref or str(event.get("result") or "") in _REJECTED_RESULTS:
            continue
        target_ref = str(event.get("intended_ref") or "")
        target = people.get(target_ref)
        if not target_ref or not isinstance(target, Mapping):
            continue
        breached = breach_hostile_commitments(
            out,
            actor_ref=player_ref,
            target_ref=target_ref,
            target_faction_ref=str(target.get("faction_ref") or ""),
            targeting_intent=str(event.get("targeting_intent") or "disable"),
            poison_ref=str(event.get("poison_ref") or ""),
        )
        out = breached["state_after"]
    return copy.deepcopy(dict(out))


def _protected_projection(projection: Mapping[str, Any] | None) -> bool:
    beats = projection.get("beats", []) if isinstance(projection, Mapping) and isinstance(projection.get("beats"), list) else []
    return any(isinstance(row, Mapping) and bool(row.get("must_narrate_before_next_decision")) for row in beats)


def _merge_projections(
    projections: Sequence[Mapping[str, Any]], *, exchanges: int, stop_reason: str
) -> dict[str, Any]:
    beats: list[dict[str, Any]] = []
    rules: list[str] = []
    current_visibility: dict[str, Any] = {}
    for projection in projections:
        if not isinstance(projection, Mapping):
            continue
        current = projection.get("current_visibility")
        if isinstance(current, Mapping):
            current_visibility = copy.deepcopy(dict(current))
        for beat in projection.get("beats", []) if isinstance(projection.get("beats"), list) else []:
            if isinstance(beat, Mapping):
                beats.append(copy.deepcopy(dict(beat)))
        for rule in projection.get("narration_rules", []) if isinstance(projection.get("narration_rules"), list) else []:
            if isinstance(rule, str) and rule not in rules:
                rules.append(rule)
    protected = [row for row in beats if row.get("salience") == "protected"]
    ordinary = [row for row in beats if row.get("salience") != "protected"][-12:]
    bounded = sorted(
        [*protected, *ordinary],
        key=lambda row: (int(row.get("at_ms", 0) or 0), 0 if row.get("salience") == "protected" else 1),
    )[-18:]
    return {
        "schema": "shinobi-combat-narrative-projection-1.1",
        "chronology": "mechanical_event_time",
        "scope": "declared_player_combat_span",
        "exchanges_resolved": int(exchanges),
        "scope_stop_reason": stop_reason,
        "beats": bounded,
        "protected_salience_count": len(protected),
        "current_visibility": current_visibility,
        "narration_rules": rules,
    }


def _requested_scope_complete(
    *, exchanges: int, combat: Mapping[str, Any], start_elapsed: int,
    requested_count: int | None, duration_ms: int | None, until_resolution: bool,
) -> bool:
    if requested_count is not None:
        return exchanges >= requested_count
    if duration_ms is not None:
        return max(0, int(combat.get("elapsed_ms", 0)) - start_elapsed) >= duration_ms
    if until_resolution:
        return str(combat.get("status") or "") != "active"
    return exchanges >= 1


def _adaptive_qi_allocation(
    *, people: Mapping[str, Mapping[str, Any]], player_ref: str,
    failure_streak: int, threshold: int, targeting_intent: str,
    until_resolution: bool,
) -> dict[str, int] | None:
    """Return a bounded emergency flow for delegated lethal pursuit after failure.

    The reserve threshold is authored on the active personal doctrine. Exact Qi
    mechanics still own safe-flow delivery, resource limitation and final spend.
    """
    if failure_streak < threshold or not until_resolution or targeting_intent != "lethal":
        return None
    actor = people.get(player_ref)
    if not isinstance(actor, Mapping):
        return None
    doctrine_ref = actor.get("combat_doctrine_ref")
    doctrine = resolve_individual_doctrine(doctrine_ref) if isinstance(doctrine_ref, str) else None
    resources = doctrine.get("resource_discipline", {}) if isinstance(doctrine, Mapping) else {}
    reserve_percent = resources.get("adaptive_failure_qi_reserve_percent") if isinstance(resources, Mapping) else None
    if not isinstance(reserve_percent, int) or isinstance(reserve_percent, bool):
        return None
    reserve_percent = max(0, min(100, reserve_percent))
    qi = max(0, int(actor.get("qi", 0)))
    control = max(0, int(actor.get("qi_control", 0)))
    current = person_current_qi_milli(actor)
    reserve = qi * 1000 * reserve_percent // 100
    if qi <= 0 or control <= 0 or current <= reserve:
        return None
    safe_flow = max(0, safe_flow_milli_per_second(qi, control))
    if safe_flow <= 0:
        return None
    flow = max(1, safe_flow * 3 // 4)
    movement = max(1, flow * 55 // 100)
    body = max(1, flow * 35 // 100)
    sensing = max(0, flow - movement - body)
    out = {"movement": movement, "body": body}
    if sensing > 0:
        out["sensing"] = sensing
    return out


def _wasted_auto_poison_projectile(event: Mapping[str, Any] | None) -> bool:
    if not isinstance(event, Mapping):
        return False
    if str(event.get("action_kind") or "") not in _PROJECTILE_ACTIONS:
        return False
    commit = event.get("resource_commit")
    if not isinstance(commit, Mapping) or not bool(commit.get("poison_dose_consumed")):
        return False
    if not str(event.get("poison_ref") or ""):
        return False
    return str(event.get("result") or "") in _DEFINITE_FAILURE_RESULTS


def _movement_rejection(exc: BaseException) -> bool:
    text = str(exc)
    return text in {
        "jianghu_combat_tactical_movement_unavailable",
        "jianghu_combat_tactical_movement_path_blocked",
        "jianghu_combat_tactical_movement_requires_melee",
    }


def _resolve_with_optional_movement(
    base_resolver: Callable[..., Mapping[str, Any]],
    *, attempt: dict[str, Any], player_ref: str,
) -> Mapping[str, Any]:
    movement_intent = str(attempt.pop(_ADAPTIVE_MOVEMENT_SENTINEL, "") or "")
    if not movement_intent:
        return base_resolver(**attempt)
    from shinobi_runtime.api.combat_tactical_movement_integrity import _MOVEMENT_CONTEXT

    token = _MOVEMENT_CONTEXT.set({"actor_ref": player_ref, "movement_intent": movement_intent})
    try:
        return base_resolver(**attempt)
    finally:
        _MOVEMENT_CONTEXT.reset(token)


def _combat_tally(combat: Mapping[str, Any], player_ref: str) -> Mapping[str, Any]:
    tallies = combat.get("player_combat_tallies", {}) if isinstance(combat.get("player_combat_tallies"), Mapping) else {}
    row = tallies.get(player_ref)
    return row if isinstance(row, Mapping) else {}


def _normalize_span_combat_information(
    *, initial_combat: Mapping[str, Any], final_combat: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]], player_ref: str,
    current_information: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project whole-span resolution counters from authoritative final state."""
    info = copy.deepcopy(dict(current_information)) if isinstance(current_information, Mapping) else {}
    before = _combat_tally(initial_combat, player_ref)
    after = _combat_tally(final_combat, player_ref)
    defeats_before = max(0, int(before.get("confirmed_defeats", 0) or 0))
    kills_before = max(0, int(before.get("confirmed_kills", 0) or 0))
    defeats_after = max(defeats_before, int(after.get("confirmed_defeats", defeats_before) or defeats_before))
    kills_after = max(kills_before, int(after.get("confirmed_kills", kills_before) or kills_before))
    info["player_confirmed_defeats_this_resolution"] = defeats_after - defeats_before
    info["player_confirmed_kills_this_resolution"] = kills_after - kills_before
    if after:
        info["player_confirmed_defeats_encounter"] = defeats_after
        info["player_confirmed_kills_encounter"] = kills_after

    hostile_refs = set(_enemy_refs(final_combat, player_ref)) | set(_enemy_refs(initial_combat, player_ref))
    withdrawals = {
        str(event.get("actor_ref") or "")
        for event in events
        if isinstance(event, Mapping)
        and str(event.get("result") or "") == "withdrew_from_combat"
        and str(event.get("actor_ref") or "") in hostile_refs
    }
    info["confirmed_hostile_withdrawals_this_resolution"] = len(withdrawals)
    info["observed_escaped"] = len(withdrawals)
    return info


def adaptive_standing_span(
    base_resolver: Callable[..., Mapping[str, Any]],
    *,
    fallback: Callable[..., Mapping[str, Any]],
    max_elapsed_ms: int = 300_000,
    standing_exchange_limit: int = 16,
    **kwargs: Any,
) -> Mapping[str, Any]:
    """Resolve a delegated multi-exchange player span with bounded tactical memory.

    Each exact exchange is still resolved by ``base_resolver``. Only omitted
    tactical details may change. Returned event history is kept transaction-local
    and is never written as a second campaign authority.
    """
    required = {"combat", "people", "equipment_ledger", "player_ref", "social_state", "raw_target_ref", "raw_action_kind", "raw_weapon_ref", "hit_zone"}
    if not required.issubset(kwargs):
        return fallback(base_resolver, **kwargs)

    requested_count = max(1, int(kwargs["exchange_count"])) if kwargs.get("exchange_count") is not None else None
    duration_ms = max(1, int(kwargs["duration_seconds"])) * 1000 if kwargs.get("duration_seconds") is not None else None
    until_resolution = bool(kwargs.get("until_resolution"))
    multi_exchange = until_resolution or duration_ms is not None or (requested_count is not None and requested_count > 1)
    delegated = (
        str(kwargs.get("raw_target_ref") or "auto") in _AUTO_TARGETS
        or str(kwargs.get("raw_action_kind") or "attack") in _AUTO_ACTIONS
        or str(kwargs.get("raw_weapon_ref") or "auto") in _AUTO_WEAPONS
    )
    if not multi_exchange or not delegated:
        return fallback(base_resolver, **kwargs)

    original = copy.deepcopy(dict(kwargs))
    initial_combat = copy.deepcopy(dict(kwargs["combat"]))
    combat_cursor = copy.deepcopy(initial_combat)
    people_cursor = {str(ref): copy.deepcopy(dict(person)) for ref, person in kwargs["people"].items()}
    ledger_cursor = copy.deepcopy(dict(kwargs["equipment_ledger"]))
    social_cursor = copy.deepcopy(dict(kwargs["social_state"]))
    player_ref = str(kwargs["player_ref"])
    start_elapsed = max(0, int(combat_cursor.get("elapsed_ms", 0)))
    threshold = intelligence_adaptation_threshold(people_cursor, player_ref)
    stagnation_limit = max(6, threshold * 3)
    max_exchanges = (
        requested_count if requested_count is not None
        else max(1, int(kwargs.get("frontier_exchanges", 160))) if not until_resolution
        else min(max(1, int(kwargs.get("frontier_exchanges", standing_exchange_limit))), max(1, int(standing_exchange_limit)))
    )

    all_events: list[Mapping[str, Any]] = []
    projections: list[Mapping[str, Any]] = []
    exchanges = 0
    failure_streak = 0
    previous_event: Mapping[str, Any] | None = None
    previous_projection: Mapping[str, Any] | None = None
    last_result: Mapping[str, Any] | None = None
    stop_reason = "scope_complete"
    movement_used = False
    suppress_auto_poison = False

    while str(combat_cursor.get("status") or "") == "active":
        if _requested_scope_complete(
            exchanges=exchanges, combat=combat_cursor, start_elapsed=start_elapsed,
            requested_count=requested_count, duration_ms=duration_ms, until_resolution=until_resolution,
        ):
            break
        if exchanges >= max_exchanges:
            stop_reason = "execution_frontier"
            break
        if exchanges > 0 and not _explicit_target_available({**original, "combat": combat_cursor, "people": people_cursor}):
            stop_reason = "explicit_target_unavailable"
            break
        if exchanges > 0 and str(original.get("raw_target_ref") or "auto") in _AUTO_TARGETS:
            if not visible_active_enemies(combat_cursor, people_cursor, player_ref):
                stop_reason = "no_lawfully_known_target"
                break

        adaptive_candidates: list[dict[str, Any]] = []
        if failure_streak >= threshold:
            adaptive_candidates = adaptive_override_candidates(
                original_kwargs=original,
                combat=combat_cursor,
                people=people_cursor,
                player_ref=player_ref,
                previous_event=previous_event,
                previous_projection=previous_projection,
                movement_already_used=movement_used,
            )
            if adaptive_candidates:
                round_index = max(0, failure_streak // threshold - 1)
                offset = round_index % len(adaptive_candidates)
                adaptive_candidates = adaptive_candidates[offset:] + adaptive_candidates[:offset]

        candidate_overrides = [*adaptive_candidates, {}]
        resolved: Mapping[str, Any] | None = None
        used_override: Mapping[str, Any] = {}
        last_error: Exception | None = None
        for override in candidate_overrides:
            attempt = dict(original)
            attempt.update(override)
            attempt.update({
                "combat": combat_cursor,
                "people": people_cursor,
                "equipment_ledger": ledger_cursor,
                "social_state": social_cursor,
                "exchange_count": 1,
                "duration_seconds": None,
                "until_resolution": until_resolution,
                "frontier_exchanges": 1,
                "rally_allies": bool(original.get("rally_allies")) if exchanges == 0 else False,
                "player_improvised_weapon_state": original.get("player_improvised_weapon_state") if exchanges == 0 else None,
            })
            if suppress_auto_poison and original.get("explicit_poison_ref") in (None, "", "auto"):
                attempt["poison_auto"] = False
            if original.get("explicit_qi_allocation_milli") is None and bool(original.get("qi_auto", True)):
                emergency_qi = _adaptive_qi_allocation(
                    people=people_cursor,
                    player_ref=player_ref,
                    failure_streak=failure_streak,
                    threshold=threshold,
                    targeting_intent=str(original.get("targeting_intent") or "disable"),
                    until_resolution=until_resolution,
                )
                if emergency_qi:
                    attempt["explicit_qi_allocation_milli"] = emergency_qi
                    attempt["qi_auto"] = False
            try:
                resolved = _resolve_with_optional_movement(
                    base_resolver, attempt=attempt, player_ref=player_ref,
                )
                used_override = override
                if _ADAPTIVE_MOVEMENT_SENTINEL in override:
                    movement_used = True
                break
            except ValueError as exc:
                last_error = exc
                if not override:
                    raise
            except Exception as exc:
                if _ADAPTIVE_MOVEMENT_SENTINEL in override and _movement_rejection(exc):
                    last_error = exc
                    continue
                raise
        if resolved is None:
            if last_error is not None:
                raise last_error
            raise ValueError("adaptive combat span produced no exchange")

        result_combat = resolved.get("combat_after")
        if not isinstance(result_combat, Mapping):
            return resolved
        elapsed = max(0, int(result_combat.get("elapsed_ms", 0)) - start_elapsed)
        if until_resolution and elapsed > max(1, int(max_elapsed_ms)):
            if exchanges == 0:
                raise ValueError("single combat exchange exceeds standing-span execution time frontier")
            stop_reason = "execution_frontier"
            break

        combat_cursor = copy.deepcopy(dict(result_combat))
        people_after = resolved.get("people_after")
        if isinstance(people_after, Mapping):
            people_cursor = {str(ref): copy.deepcopy(dict(person)) for ref, person in people_after.items() if isinstance(person, Mapping)}
        ledger_after = resolved.get("equipment_ledger_after")
        if isinstance(ledger_after, Mapping):
            ledger_cursor = copy.deepcopy(dict(ledger_after))

        events: list[dict[str, Any]] = []
        for raw_event in resolved.get("events", []) if isinstance(resolved.get("events"), list) else []:
            if not isinstance(raw_event, Mapping):
                continue
            event = copy.deepcopy(dict(raw_event))
            if used_override and str(event.get("actor_ref") or "") == player_ref:
                event["decision_origin"] = "player_adaptive"
            events.append(event)
        all_events.extend(events)
        projection = resolved.get("narrative_projection") if isinstance(resolved.get("narrative_projection"), Mapping) else {}
        projections.append(copy.deepcopy(dict(projection)))
        exchanges += max(1, int(resolved.get("exchanges_resolved", 1) or 1))
        last_result = resolved

        player_event = _player_attack_event(events, player_ref)
        if definite_tactical_failure(player_event):
            failure_streak += 1
        else:
            failure_streak = 0
        if (
            not suppress_auto_poison
            and original.get("explicit_poison_ref") in (None, "", "auto")
            and bool(original.get("poison_auto", True))
            and _wasted_auto_poison_projectile(player_event)
        ):
            suppress_auto_poison = True
        previous_event = copy.deepcopy(dict(player_event)) if isinstance(player_event, Mapping) else None
        previous_projection = copy.deepcopy(dict(projection))

        social_cursor = _update_social_cursor(
            social_cursor, events, combat=combat_cursor, people=people_cursor, player_ref=player_ref,
        )

        if _protected_projection(projection):
            stop_reason = "protected_player_decision"
            break
        if failure_streak >= stagnation_limit:
            stop_reason = "tactical_stagnation"
            break
        if str(combat_cursor.get("status") or "") != "active":
            stop_reason = "combat_resolved"
            break

    if last_result is None:
        return fallback(base_resolver, **kwargs)
    if str(combat_cursor.get("status") or "") != "active":
        stop_reason = "combat_resolved"
    elif stop_reason == "scope_complete" and _requested_scope_complete(
        exchanges=exchanges, combat=combat_cursor, start_elapsed=start_elapsed,
        requested_count=requested_count, duration_ms=duration_ms, until_resolution=until_resolution,
    ):
        stop_reason = "scope_complete"
    elif stop_reason == "scope_complete" and exchanges >= max_exchanges:
        stop_reason = "execution_frontier"

    continuation_required = stop_reason == "execution_frontier" and str(combat_cursor.get("status") or "") == "active"
    out = copy.deepcopy(dict(last_result))
    out.update({
        "combat_after": combat_cursor,
        "people_after": people_cursor,
        "equipment_ledger_after": ledger_cursor,
        "events": all_events,
        "exchanges_resolved": exchanges,
        "scope_stop_reason": stop_reason,
        "continuation_required": continuation_required,
        "narrative_projection": _merge_projections(projections, exchanges=exchanges, stop_reason=stop_reason),
        "combat_information": _normalize_span_combat_information(
            initial_combat=initial_combat,
            final_combat=combat_cursor,
            events=all_events,
            player_ref=player_ref,
            current_information=last_result.get("combat_information") if isinstance(last_result, Mapping) else None,
        ),
    })
    return out


__all__ = [
    "adaptive_override_candidates",
    "adaptive_standing_span",
    "definite_tactical_failure",
    "intelligence_adaptation_threshold",
    "visible_active_enemies",
]
