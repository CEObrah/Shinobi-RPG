"""Production integrity policy for exact-combat withdrawal and liveness.

This module closes a class of self-sustaining exact-combat loops without creating a
second combat authority.  The exact resolver remains the source of physical outcomes;
these wrappers repair three integration contracts around it:

* a withdrawing actor chooses an actually separating open corridor instead of the
  lowest numeric angle shared by both sides;
* a stale pending target does not masquerade as committed melee pursuit after the
  withdrawing actor has physically cleared local reach;
* a standing ``until_resolution`` span may auto-continue only when the bounded span
  makes resolution-directed progress, not merely because somebody moved.

The module also repairs the current-wound merge invariant that aggregate trauma must
never lose a previously derived functional-loss floor simply because no named
anatomical structure was resolved.
"""
from __future__ import annotations

import contextvars
import copy
import math
from typing import Any, Callable, Mapping, Sequence

_RETREAT_CONTEXT: contextvars.ContextVar[tuple[Mapping[str, Any], str] | None] = contextvars.ContextVar(
    "shinobi_retreat_context", default=None
)
_TERMINAL_STATUSES = frozenset({"dead", "unconscious", "incapacitated", "escaped"})
_MELEE_ACTIONS = frozenset({"cut", "thrust", "unarmed_strike", "staff_strike", "staff_thrust", "staff_butt_strike", "staff_sweep", "capture"})
_CLOSE_PAIR_MM = 12_000
_ESCAPE_CLEAR_MM = 6_000
_PURSUIT_HORIZON_MM = 14_000


def _statuses(combat: Mapping[str, Any], ref: str) -> set[str]:
    states = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    state = states.get(ref) if isinstance(states, Mapping) else None
    if not isinstance(state, Mapping):
        return set()
    return {str(value) for value in state.get("status_families", []) if isinstance(value, str)}


def _active(combat: Mapping[str, Any], ref: str) -> bool:
    return not bool(_statuses(combat, ref) & _TERMINAL_STATUSES)


def _side_of(combat: Mapping[str, Any], actor_ref: str) -> str | None:
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    for side_ref, members in sides.items():
        if isinstance(members, list) and actor_ref in members:
            return str(side_ref)
    return None


def _hostile_refs(combat: Mapping[str, Any], actor_ref: str) -> list[str]:
    own = _side_of(combat, actor_ref)
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    refs: list[str] = []
    for side_ref, members in sides.items():
        if str(side_ref) == own or not isinstance(members, list):
            continue
        refs.extend(str(ref) for ref in members if isinstance(ref, str) and _active(combat, str(ref)))
    return refs


def _distance_xy(ax: int, ay: int, bx: int, by: int) -> int:
    return math.isqrt((ax - bx) * (ax - bx) + (ay - by) * (ay - by))


def separating_retreat_corridors(
    base: Callable[..., Sequence[Mapping[str, Any]]],
    positions: Mapping[str, Mapping[str, Any]],
    *,
    actor_ref: str,
    **kwargs: Any,
) -> tuple[dict[str, int], ...]:
    """Return the safest currently open retreat corridor first and exclusively.

    ``_disengage_step`` historically sorts its candidates by raw angle and takes the
    first one.  Returning one best lawful candidate preserves that resolver contract
    while replacing the accidental global eastward preference with hostile separation.
    Outside a live disengage call the geometry helper is unchanged.
    """
    raw = base(positions, actor_ref=actor_ref, **kwargs)
    rows = [dict(row) for row in raw if isinstance(row, Mapping)]
    context = _RETREAT_CONTEXT.get()
    if not rows or context is None or context[1] != actor_ref:
        return tuple(rows)
    combat = context[0]
    actor = positions.get(actor_ref)
    if not isinstance(actor, Mapping):
        return tuple(rows)
    zone = actor.get("zone_ref")
    hostiles: list[Mapping[str, Any]] = []
    for ref in _hostile_refs(combat, actor_ref):
        pos = positions.get(ref)
        if isinstance(pos, Mapping) and pos.get("zone_ref") == zone:
            hostiles.append(pos)
    if not hostiles:
        return tuple(rows)

    def score(row: Mapping[str, Any]) -> tuple[int, int, int]:
        ex = int(row.get("end_x_mm", actor.get("x_mm", 0)))
        ey = int(row.get("end_y_mm", actor.get("y_mm", 0)))
        distances = [
            _distance_xy(ex, ey, int(pos.get("x_mm", 0)), int(pos.get("y_mm", 0)))
            for pos in hostiles
        ]
        # First maximize the nearest hostile, then overall clearance.  Final angle
        # tie-break keeps the result deterministic without making angle the tactic.
        return (min(distances), sum(distances), -int(row.get("angle_mdeg", 0)))

    return (max(rows, key=score),)


def _genuine_pursuit(combat: Mapping[str, Any], actor_ref: str) -> bool:
    """Whether a current hostile is physically closing or has a credible melee commit."""
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    actor = positions.get(actor_ref)
    if not isinstance(actor, Mapping):
        return False
    ax, ay = int(actor.get("x_mm", 0)), int(actor.get("y_mm", 0))
    zone = actor.get("zone_ref")
    pending = combat.get("_pending_actions", {}) if isinstance(combat.get("_pending_actions"), Mapping) else {}
    for ref in _hostile_refs(combat, actor_ref):
        pos = positions.get(ref)
        if not isinstance(pos, Mapping) or pos.get("zone_ref") != zone:
            continue
        hx, hy = int(pos.get("x_mm", 0)), int(pos.get("y_mm", 0))
        distance = _distance_xy(ax, ay, hx, hy)
        if distance < _ESCAPE_CLEAR_MM:
            return True
        if distance > _PURSUIT_HORIZON_MM:
            continue

        # Velocity toward the withdrawing actor is direct physical evidence of pursuit.
        vx, vy = int(pos.get("vx_mmps", 0)), int(pos.get("vy_mmps", 0))
        toward_dot = (ax - hx) * vx + (ay - hy) * vy
        if toward_dot > max(1, distance) * 200:
            return True
        if str(pos.get("stance") or "") == "approaching" and toward_dot > 0:
            return True

        row = pending.get(ref) if isinstance(pending, Mapping) else None
        if isinstance(row, Mapping) and str(row.get("target_ref") or "") == actor_ref:
            action_kind = str(row.get("action_kind") or row.get("kind") or "")
            if action_kind in _MELEE_ACTIONS:
                return True
    return False


def disengage_with_integrity(base: Callable[..., Mapping[str, Any]], **kwargs: Any) -> Mapping[str, Any]:
    """Run one exact disengage using separating corridors and bounded pursuit truth."""
    combat = kwargs.get("combat")
    actor_ref = str(kwargs.get("actor_ref") or "")
    if not isinstance(combat, Mapping) or not actor_ref:
        return base(**kwargs)
    token = _RETREAT_CONTEXT.set((combat, actor_ref))
    try:
        result = base(**kwargs)
    finally:
        _RETREAT_CONTEXT.reset(token)
    if not isinstance(result, Mapping) or bool(result.get("escaped")):
        return result
    movement = result.get("movement") if isinstance(result.get("movement"), Mapping) else {}
    nearest = max(0, int(movement.get("nearest_enemy_mm", 0) or 0))
    if nearest < _ESCAPE_CLEAR_MM or _genuine_pursuit(combat, actor_ref):
        return result

    # The base step has already committed the lawful movement.  Only repair the
    # escape classification that a stale pending-target veto could incorrectly deny.
    if isinstance(combat, dict):
        states = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
        state = states.get(actor_ref) if isinstance(states, Mapping) else None
        if isinstance(state, dict):
            statuses = {str(value) for value in state.get("status_families", []) if isinstance(value, str)}
            statuses.add("escaped")
            state["status_families"] = sorted(statuses)
            start_ms = max(0, int(movement.get("start_ms", kwargs.get("start_ms", 0)) or 0))
            duration_ms = max(0, int(movement.get("duration_ms", kwargs.get("duration_ms", 0)) or 0))
            state["escaped_at_ms"] = start_ms + duration_ms
    out = copy.deepcopy(dict(result))
    out["escaped"] = True
    out["reason"] = "cleared_opponent_reach"
    if isinstance(out.get("movement"), dict):
        out["movement"]["escape_reclassified_from_stale_pursuit_veto"] = True
    return out


def _terminal_snapshot(combat: Mapping[str, Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    states = combat.get("combatants", {}) if isinstance(combat.get("combatants"), Mapping) else {}
    rows: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(states, Mapping):
        for ref in sorted(str(key) for key in states if isinstance(key, str)):
            terminal = sorted(_statuses(combat, ref) & _TERMINAL_STATUSES)
            rows.append((ref, tuple(terminal)))
    return tuple(rows)


def _pressure_metrics(combat: Mapping[str, Any]) -> tuple[int, int]:
    """Return close opposing-pair count and a bounded contact-pressure score."""
    positions = combat.get("positions", {}) if isinstance(combat.get("positions"), Mapping) else {}
    sides = combat.get("sides", {}) if isinstance(combat.get("sides"), Mapping) else {}
    side_rows = [
        [str(ref) for ref in members if isinstance(ref, str) and _active(combat, str(ref))]
        for members in sides.values() if isinstance(members, list)
    ]
    if len(side_rows) < 2:
        return (0, 0)
    close_pairs = 0
    pressure = 0
    for index, left in enumerate(side_rows):
        for right in side_rows[index + 1:]:
            for a_ref in left:
                a = positions.get(a_ref)
                if not isinstance(a, Mapping):
                    continue
                for b_ref in right:
                    b = positions.get(b_ref)
                    if not isinstance(b, Mapping) or a.get("zone_ref") != b.get("zone_ref"):
                        continue
                    d = _distance_xy(
                        int(a.get("x_mm", 0)), int(a.get("y_mm", 0)),
                        int(b.get("x_mm", 0)), int(b.get("y_mm", 0)),
                    )
                    if d <= _CLOSE_PAIR_MM:
                        close_pairs += 1
                        pressure += _CLOSE_PAIR_MM - d
    return close_pairs, pressure


def _material_resolution_event(event: Mapping[str, Any]) -> bool:
    result = str(event.get("result") or "")
    if result in {"dead", "incapacitated", "escaped", "withdrew_from_combat", "support_treatment_completed"}:
        return True
    physiology = event.get("physiology") if isinstance(event.get("physiology"), Mapping) else {}
    if str(physiology.get("status") or "") in {"dead", "incapacitated", "unconscious"}:
        return True
    damage = event.get("damage") if isinstance(event.get("damage"), Mapping) else {}
    wound = damage.get("wound") if isinstance(damage.get("wound"), Mapping) else {}
    if wound:
        return (
            max(0, int(wound.get("severity", 0) or 0)) >= 20
            or max(0, int(wound.get("bleeding_ml_per_min", 0) or 0)) > 0
            or max(0, int(wound.get("function_loss_pct", 0) or 0)) > 0
            or max(
                max(0, int(wound.get("fracture", 0) or 0)),
                max(0, int(wound.get("tendon_damage", 0) or 0)),
                max(0, int(wound.get("nerve_damage", 0) or 0)),
                max(0, int(wound.get("organ_trauma", 0) or 0)),
            ) > 0
        )
    return False


def _resolution_progress(
    before: Mapping[str, Any], after: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> bool:
    if str(after.get("status") or "") != "active":
        return True
    if _terminal_snapshot(before) != _terminal_snapshot(after):
        return True
    if any(_material_resolution_event(event) for event in events if isinstance(event, Mapping)):
        return True
    before_pairs, before_pressure = _pressure_metrics(before)
    after_pairs, after_pressure = _pressure_metrics(after)
    if after_pairs < before_pairs:
        return True
    if before_pressure > 0 and after_pressure <= before_pressure * 4 // 5:
        return True
    return False


def resolution_progress_guard(
    base_resolver: Callable[..., Mapping[str, Any]], **kwargs: Any
) -> Mapping[str, Any]:
    """Stop automatic standing-combat chaining when a bounded span does not converge."""
    before = kwargs.get("combat")
    result = base_resolver(**kwargs)
    if not bool(kwargs.get("until_resolution")):
        return result
    if kwargs.get("exchange_count") is not None or kwargs.get("duration_seconds") is not None:
        return result
    if str(result.get("scope_stop_reason") or "") != "execution_frontier":
        return result
    after = result.get("combat_after") if isinstance(result.get("combat_after"), Mapping) else None
    if not isinstance(before, Mapping) or not isinstance(after, Mapping) or str(after.get("status") or "") != "active":
        return result
    events = result.get("events", []) if isinstance(result.get("events"), list) else []
    if _resolution_progress(before, after, [event for event in events if isinstance(event, Mapping)]):
        return result

    out = copy.deepcopy(dict(result))
    out["scope_stop_reason"] = "stagnation_checkpoint"
    out["continuation_required"] = False
    projection = out.get("narrative_projection")
    if isinstance(projection, dict):
        projection["scope_stop_reason"] = "stagnation_checkpoint"
        rules = projection.setdefault("narration_rules", [])
        if isinstance(rules, list):
            rule = (
                "The bounded combat span did not reduce opposing contact pressure, produce a material injury, "
                "complete treatment, or change a terminal combat state; return control instead of auto-continuing."
            )
            if rule not in rules:
                rules.append(rule)
    return out


def _coarse_trauma_function_loss(wound: Mapping[str, Any]) -> int:
    cut = max(0, int(wound.get("cut", 0) or 0))
    pierce = max(0, int(wound.get("pierce", 0) or 0))
    blunt = max(0, int(wound.get("blunt", 0) or 0))
    penetration = max(0, int(wound.get("penetration", 0) or 0))
    fracture = max(0, int(wound.get("fracture", 0) or 0))
    tendon = max(0, int(wound.get("tendon_damage", 0) or 0))
    nerve = max(0, int(wound.get("nerve_damage", 0) or 0))
    tissue = cut + pierce + blunt // 2 + penetration
    return min(100, max(fracture, tendon, nerve) // 2 + min(50, tissue // 5))


def merge_current_wound_with_integrity(
    base: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve cumulative trauma-derived function loss through current-wound compaction."""
    merged = copy.deepcopy(dict(base(existing, incoming)))
    merged["function_loss_pct"] = max(
        max(0, min(100, int(merged.get("function_loss_pct", 0) or 0))),
        _coarse_trauma_function_loss(merged),
    )
    return merged


def install_combat_liveness_integrity() -> None:
    """Install current-combat liveness and injury invariants once per process."""
    from shinobi_runtime.commands import jianghu_extended as extended
    from shinobi_runtime.martial_world import exact_combat as exact
    from shinobi_runtime.martial_world import health

    if not bool(getattr(exact, "_combat_retreat_integrity_installed", False)):
        base_corridors = exact.open_retreat_corridors
        base_disengage = exact._disengage_step

        def corridors(positions: Mapping[str, Mapping[str, Any]], *, actor_ref: str, **kwargs: Any):
            return separating_retreat_corridors(base_corridors, positions, actor_ref=actor_ref, **kwargs)

        def disengage(**kwargs: Any):
            return disengage_with_integrity(base_disengage, **kwargs)

        exact.open_retreat_corridors = corridors
        exact._disengage_step = disengage
        exact._combat_retreat_integrity_installed = True

    if not bool(getattr(health, "_current_wound_merge_integrity_installed", False)):
        base_merge = health._merge_current_wound

        def merge(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
            return merge_current_wound_with_integrity(base_merge, existing, incoming)

        health._merge_current_wound = merge
        health._current_wound_merge_integrity_installed = True

    if not bool(getattr(extended, "_resolution_progress_guard_installed", False)):
        base_span = extended._resolve_player_combat_span

        def span(**kwargs: Any) -> Mapping[str, Any]:
            return resolution_progress_guard(base_span, **kwargs)

        extended._resolve_player_combat_span = span
        extended._resolution_progress_guard_installed = True


__all__ = [
    "disengage_with_integrity",
    "install_combat_liveness_integrity",
    "merge_current_wound_with_integrity",
    "resolution_progress_guard",
    "separating_retreat_corridors",
]
