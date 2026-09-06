"""Exact-combat integrity for player-authored tactical approach movement.

The public combat command historically exposed attack details and disengagement,
but not a way to express a committed lateral entry such as "step off-line, make
the spear turn, then close." Exact combat already owns physical positions,
movement speed, collision, attack scheduling, and melee approach time. This
adapter exposes that missing player-control seam without creating a second
movement resolver or granting free displacement.

A movement intent applies to one melee exchange only. The normal exact action is
still scheduled for every fighter. The player's strike is delayed by the real
movement window, and the same bounded approach-distance budget is spent first on
lateral footwork and then on closing. Earlier hostile contacts therefore resolve
before the reposition completes; later contacts consume the updated geometry.
"""
from __future__ import annotations

import copy
import math
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.combat.geometry import facing_to_target_mdeg, path_clear, planar_distance_mm
from shinobi_runtime.combat.models import ActionProfile, PositionState
from shinobi_runtime.commands.specs import CommandSpec, CommandVariant

_ALLOWED_MOVEMENT_INTENTS = frozenset({"lateral", "lateral_left", "lateral_right"})
_MOVEMENT_CONTEXT: ContextVar[Mapping[str, str] | None] = ContextVar(
    "shinobi_combat_tactical_movement", default=None
)


def _extend_combat_command_contract() -> None:
    """Add one optional tactical movement field to the existing exchange variant."""
    from shinobi_runtime.commands import specs

    spec = specs.COMMAND_SPECS.get("jianghu_combat_resolution")
    if not isinstance(spec, CommandSpec) or not isinstance(spec.variants, Mapping):
        raise RuntimeError("jianghu combat command spec unavailable")
    exchange = spec.variants.get("exchange")
    if not isinstance(exchange, CommandVariant):
        raise RuntimeError("jianghu combat exchange variant unavailable")
    if "movement_intent" in exchange.optional_fields:
        return

    variants = dict(spec.variants)
    variants["exchange"] = CommandVariant(
        required_fields=exchange.required_fields,
        optional_fields=(*exchange.optional_fields, "movement_intent"),
        payload_hints={
            **dict(exchange.payload_hints or {}),
            "movement_intent": "<lateral|lateral_left|lateral_right>",
        },
    )
    specs.COMMAND_SPECS["jianghu_combat_resolution"] = CommandSpec(
        required_fields=spec.required_fields,
        optional_fields=spec.optional_fields,
        summary=(
            spec.summary
            + " Optional movement_intent spends real melee approach time on bounded lateral footwork before the strike; it never grants free displacement or applies to ranged attacks."
        ),
        payload_hints=spec.payload_hints,
        availability=spec.availability,
        variants=variants,
    )


def _position_with(
    position: PositionState,
    *,
    x_mm: int,
    y_mm: int,
    facing_mdeg: int,
    vx_mmps: int,
    vy_mmps: int,
    stance: str,
) -> PositionState:
    return PositionState(
        zone_ref=position.zone_ref,
        elevation_mm=position.elevation_mm,
        cover_milli=position.cover_milli,
        x_mm=int(x_mm),
        y_mm=int(y_mm),
        facing_mdeg=int(facing_mdeg) % 360_000,
        body_radius_mm=position.body_radius_mm,
        vx_mmps=int(vx_mmps),
        vy_mmps=int(vy_mmps),
        stance=str(stance),
    )


def _candidate_clearance_mm(
    *,
    x_mm: int,
    y_mm: int,
    positions: Mapping[str, Mapping[str, Any]],
    attacker_ref: str,
    defender_ref: str,
    body_refs: Sequence[str],
) -> int:
    probe = {"x_mm": int(x_mm), "y_mm": int(y_mm)}
    distances = []
    for ref in body_refs:
        if ref in {attacker_ref, defender_ref}:
            continue
        row = positions.get(ref)
        if isinstance(row, Mapping):
            distances.append(planar_distance_mm(probe, row))
    return min(distances, default=1_000_000_000)


def _lateral_waypoint(
    *,
    intent: str,
    attacker_ref: str,
    defender_ref: str,
    positions: Mapping[str, Mapping[str, Any]],
    attacker_position: PositionState,
    defender_position: PositionState,
    lateral_budget_mm: int,
    lateral_time_ms: int,
    body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]],
) -> tuple[PositionState, str, int] | None:
    """Return the largest lawful lateral waypoint inside the physical budget."""
    if lateral_budget_mm <= 0:
        return None

    dx = defender_position.x_mm - attacker_position.x_mm
    dy = defender_position.y_mm - attacker_position.y_mm
    bearing = math.atan2(dy, dx)
    requested = (
        ("left", 1),
        ("right", -1),
    )
    if intent == "lateral_left":
        requested = (("left", 1),)
    elif intent == "lateral_right":
        requested = (("right", -1),)

    current = dict(positions)
    current[attacker_ref] = attacker_position.to_record()
    candidates: list[tuple[int, int, str, PositionState]] = []
    for label, sign in requested:
        angle = bearing + sign * math.pi / 2.0
        for fraction_milli in (1000, 750, 500, 250):
            distance = lateral_budget_mm * fraction_milli // 1000
            if distance <= 0:
                continue
            x_mm = attacker_position.x_mm + int(round(math.cos(angle) * distance))
            y_mm = attacker_position.y_mm + int(round(math.sin(angle) * distance))
            if not path_clear(
                current,
                actor_ref=attacker_ref,
                end_x_mm=x_mm,
                end_y_mm=y_mm,
                body_refs=body_refs,
                obstacles=obstacles,
            ):
                continue
            elapsed = max(1, int(lateral_time_ms) * distance // max(1, lateral_budget_mm))
            waypoint = _position_with(
                attacker_position,
                x_mm=x_mm,
                y_mm=y_mm,
                facing_mdeg=int(round(math.degrees(angle) * 1000)) % 360_000,
                vx_mmps=(x_mm - attacker_position.x_mm) * 1000 // elapsed,
                vy_mmps=(y_mm - attacker_position.y_mm) * 1000 // elapsed,
                stance=f"lateral_approach_{label}",
            )
            clearance = _candidate_clearance_mm(
                x_mm=x_mm,
                y_mm=y_mm,
                positions=positions,
                attacker_ref=attacker_ref,
                defender_ref=defender_ref,
                body_refs=body_refs,
            )
            candidates.append((clearance, distance, label, waypoint))
            break

    if not candidates:
        return None
    clearance, distance, label, waypoint = max(
        candidates,
        key=lambda row: (row[0], row[1], 1 if row[2] == "left" else 0),
    )
    del clearance
    return waypoint, label, distance


def _wrap_melee_approach() -> None:
    from shinobi_runtime.martial_world import exact_combat as exact

    base_close = exact.close_attacker_into_reach

    def tactical_close_attacker_into_reach(*args: Any, **kwargs: Any):
        context = _MOVEMENT_CONTEXT.get()
        attacker_ref = str(kwargs.get("attacker_ref") or "")
        profile = kwargs.get("profile")
        params = (
            profile.effect_parameters
            if isinstance(profile, ActionProfile) and isinstance(profile.effect_parameters, Mapping)
            else {}
        )
        intent = str(params.get("tactical_movement_intent") or "")
        if (
            not isinstance(context, Mapping)
            or attacker_ref != str(context.get("actor_ref") or "")
            or intent not in _ALLOWED_MOVEMENT_INTENTS
        ):
            return base_close(*args, **kwargs)

        defender_ref = str(kwargs.get("defender_ref") or "")
        positions = kwargs.get("positions")
        attacker_position = kwargs.get("attacker_position")
        defender_position = kwargs.get("defender_position")
        body_refs = kwargs.get("body_refs") or ()
        obstacles = kwargs.get("obstacles") or ()
        if (
            not isinstance(positions, Mapping)
            or not isinstance(attacker_position, PositionState)
            or not isinstance(defender_position, PositionState)
        ):
            return base_close(*args, **kwargs)

        total_budget = max(0, int(params.get("approach_distance_mm", 0)))
        total_time = max(0, int(params.get("approach_time_ms", 0)))
        if total_budget <= 0 or total_time <= 0:
            raise CommandRejectedError("jianghu_combat_tactical_movement_unavailable")

        lateral_budget = min(1500, total_budget * 400 // 1000)
        lateral_time = max(1, total_time * lateral_budget // max(1, total_budget))
        selected = _lateral_waypoint(
            intent=intent,
            attacker_ref=attacker_ref,
            defender_ref=defender_ref,
            positions=positions,
            attacker_position=attacker_position,
            defender_position=defender_position,
            lateral_budget_mm=lateral_budget,
            lateral_time_ms=lateral_time,
            body_refs=tuple(str(ref) for ref in body_refs if isinstance(ref, str)),
            obstacles=tuple(row for row in obstacles if isinstance(row, Mapping)),
        )
        if selected is None:
            raise CommandRejectedError("jianghu_combat_tactical_movement_path_blocked")

        waypoint, side, lateral_distance = selected
        remaining_budget = max(0, total_budget - lateral_distance)
        remaining_time = max(0, total_time - lateral_time * lateral_distance // max(1, lateral_budget))
        staged_positions = dict(positions)
        staged_positions[attacker_ref] = waypoint.to_record()

        follow_params = dict(params)
        follow_params["approach_distance_mm"] = remaining_budget
        follow_params["approach_time_ms"] = remaining_time
        follow_profile = ActionProfile(**{**profile.__dict__, "effect_parameters": follow_params})
        direct_position, direct = base_close(
            attacker_ref=attacker_ref,
            defender_ref=defender_ref,
            positions=staged_positions,
            attacker_position=waypoint,
            defender_position=defender_position,
            attacker_capability=kwargs.get("attacker_capability"),
            profile=follow_profile,
            body_refs=body_refs,
            obstacles=obstacles,
        )
        direct_distance = max(0, int(direct.get("distance_mm", 0))) if isinstance(direct, Mapping) else 0
        final = direct_position if bool(direct.get("moved")) else waypoint
        final_distance = planar_distance_mm(final.to_record(), defender_position.to_record())
        reach = exact.physical_reach_mm(profile)
        if final_distance <= max(0, int(reach)):
            facing = facing_to_target_mdeg(final.to_record(), defender_position.to_record())
            final = _position_with(
                final,
                x_mm=final.x_mm,
                y_mm=final.y_mm,
                facing_mdeg=facing,
                vx_mmps=final.vx_mmps,
                vy_mmps=final.vy_mmps,
                stance=f"lateral_entry_{side}",
            )
            reason = "closed_into_melee_reach"
        else:
            reason = "partial_committed_approach"

        return final, {
            "moved": True,
            "reason": reason,
            "movement_intent": intent,
            "lateral_side": side,
            "lateral_distance_mm": lateral_distance,
            "direct_distance_mm": direct_distance,
            "distance_mm": lateral_distance + direct_distance,
            "approach_time_ms": total_time,
            "remaining_mm": max(0, final_distance - max(0, int(reach))),
            "waypoint_x_mm": waypoint.x_mm,
            "waypoint_y_mm": waypoint.y_mm,
            "direct_reason": str(direct.get("reason") or "") if isinstance(direct, Mapping) else "",
        }

    exact.close_attacker_into_reach = tactical_close_attacker_into_reach


def _wrap_action_scheduling() -> None:
    from shinobi_runtime.martial_world import exact_combat as exact

    base_schedule = exact._schedule_action

    def tactical_schedule_action(*args: Any, **kwargs: Any):
        action = base_schedule(*args, **kwargs)
        context = _MOVEMENT_CONTEXT.get()
        actor_ref = str(kwargs.get("actor_ref") or "")
        if not isinstance(context, Mapping) or actor_ref != str(context.get("actor_ref") or ""):
            return action
        intent = str(context.get("movement_intent") or "")
        if intent not in _ALLOWED_MOVEMENT_INTENTS:
            return action
        if action.profile.delivery in {"projectile", "ranged", "thrown"}:
            raise CommandRejectedError("jianghu_combat_tactical_movement_requires_melee")

        combat = kwargs.get("combat")
        people = kwargs.get("people")
        equipment_ledger = kwargs.get("equipment_ledger")
        if not isinstance(combat, Mapping) or not isinstance(people, Mapping) or not isinstance(equipment_ledger, Mapping):
            raise CommandRejectedError("jianghu_combat_tactical_movement_unavailable")
        actor_state = combat.get("combatants", {}).get(actor_ref)
        if not isinstance(actor_state, Mapping) or actor_ref not in people:
            raise CommandRejectedError("jianghu_combat_tactical_movement_unavailable")

        params = dict(action.profile.effect_parameters)
        base_approach_ms = max(0, int(params.get("approach_time_ms", 0)))
        maximum_approach_ms = max(
            250,
            int(exact._combat_rules().get("maximum_melee_approach_ms", 2000)),
        )
        lateral_window_ms = min(650, max(250, maximum_approach_ms // 3))
        total_approach_ms = min(maximum_approach_ms, base_approach_ms + lateral_window_ms)

        capability = exact._combat_capability_for_state(
            actor_ref,
            people[actor_ref],
            equipment_ledger,
            actor_state,
            action_skill=exact._discipline_for_action(action.action_kind, action.weapon),
        )
        qi_preview = exact._qi_preview(
            person=people[actor_ref],
            combatant_state=actor_state,
            duration_ms=max(1, total_approach_ms + int(action.profile.startup_ms)),
        )
        movement_capability = exact._qi_enhanced_capability(capability, qi_preview)
        movement_speed = max(
            1,
            exact._movement_speed_for_state(
                actor_ref,
                people[actor_ref],
                equipment_ledger,
                actor_state,
                movement_capability,
            ),
        )
        total_budget = movement_speed * total_approach_ms // 1000
        actor_position = combat.get("positions", {}).get(actor_ref)
        target_position = combat.get("positions", {}).get(action.target_ref)
        if not isinstance(actor_position, Mapping) or not isinstance(target_position, Mapping):
            raise CommandRejectedError("jianghu_combat_tactical_movement_unavailable")
        direct_required = max(
            0,
            planar_distance_mm(actor_position, target_position) - exact.physical_reach_mm(action.profile),
        )
        lateral_planned = min(1500, total_budget * 400 // 1000)

        params["approach_distance_mm"] = total_budget
        params["approach_time_ms"] = total_approach_ms
        params["approach_budget_limited"] = bool(direct_required + lateral_planned > total_budget)
        params["tactical_movement_intent"] = intent
        params["tactical_movement_budget_mm"] = total_budget
        params["tactical_movement_speed_mmps"] = movement_speed
        profile = ActionProfile(**{**action.profile.__dict__, "effect_parameters": params})

        extra_ms = max(0, total_approach_ms - base_approach_ms)
        return replace(
            action,
            profile=profile,
            commit_at_ms=action.commit_at_ms + extra_ms,
            release_at_ms=action.release_at_ms + extra_ms,
            contact_at_ms=action.contact_at_ms + extra_ms,
            recovery_end_ms=action.recovery_end_ms + extra_ms,
        )

    exact._schedule_action = tactical_schedule_action


def _wrap_combat_command_reducer() -> None:
    from shinobi_runtime.commands import jianghu_extended as extended

    base_core = extended.JianghuExtendedCommandsMixin._jianghu_combat_core_resolution

    def tactical_combat_core(self: Any, command: Any, meta: Mapping[str, Any], current_time: Any):
        raw = command.payload.get("movement_intent")
        if raw in (None, ""):
            return base_core(self, command, meta, current_time)
        if command.payload.get("action") != "exchange":
            raise CommandRejectedError("jianghu_combat_tactical_movement_exchange_only")
        if not isinstance(raw, str) or raw not in _ALLOWED_MOVEMENT_INTENTS:
            raise CommandRejectedError("jianghu_combat_tactical_movement_invalid")
        exchange_count = command.payload.get("exchange_count")
        if exchange_count not in (None, 1):
            raise CommandRejectedError("jianghu_combat_tactical_movement_scope_invalid")
        if command.payload.get("duration_seconds") not in (None, 0):
            raise CommandRejectedError("jianghu_combat_tactical_movement_scope_invalid")
        if bool(command.payload.get("until_resolution")):
            raise CommandRejectedError("jianghu_combat_tactical_movement_scope_invalid")

        token = _MOVEMENT_CONTEXT.set(
            {"actor_ref": str(command.actor_id), "movement_intent": str(raw)}
        )
        try:
            return base_core(self, command, meta, current_time)
        finally:
            _MOVEMENT_CONTEXT.reset(token)

    extended.JianghuExtendedCommandsMixin._jianghu_combat_core_resolution = tactical_combat_core


def install_combat_tactical_movement_integrity() -> None:
    """Install the bounded player tactical-movement seam exactly once."""
    from shinobi_runtime.martial_world import exact_combat as exact

    if bool(getattr(exact, "_combat_tactical_movement_integrity_installed", False)):
        _extend_combat_command_contract()
        return
    _extend_combat_command_contract()
    _wrap_action_scheduling()
    _wrap_melee_approach()
    _wrap_combat_command_reducer()
    exact._combat_tactical_movement_integrity_installed = True


__all__ = ["install_combat_tactical_movement_integrity"]
