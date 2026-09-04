"""Pre-commitment same-side attack-line safety for autonomous exact combat.

Exact combat deliberately preserves physical friendly fire after an attack is
committed. This module prevents autonomous decision-makers from choosing a fresh attack
when a friendly body is already the first predicted contact and rechecks that
lane immediately before resolution so a friendly who crossed the released line
during the shared exchange can make an uncommitted autonomous shot be withheld.
Player-authored exact attack details are never rewritten.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

_INSTALLED = False


def _prospective_friendly_blocker(
    exact: Any,
    *,
    combat: Mapping[str, Any],
    action: Any,
    people: Mapping[str, Mapping[str, Any]],
    equipment_ledger: Mapping[str, Any],
) -> str | None:
    if str(getattr(action, "decision_origin", "")) == "player":
        return None

    actor_ref = str(action.actor_ref)
    target_ref = str(action.target_ref)
    positions_raw = combat.get("positions", {})
    combatants = combat.get("combatants", {})
    if not isinstance(positions_raw, Mapping) or not isinstance(combatants, Mapping):
        raise ValueError("autonomous_attack_control_state_invalid")
    if actor_ref not in positions_raw or target_ref not in positions_raw:
        raise ValueError("autonomous_attack_position_unresolved")
    actor_state = combatants.get(actor_ref)
    if not isinstance(actor_state, Mapping) or actor_ref not in people:
        raise ValueError("autonomous_attack_actor_unresolved")

    positions = copy.deepcopy(dict(positions_raw))
    body_refs = exact._present_body_refs(combat)
    profile = action.profile
    channel = "projectile" if profile.delivery in {"projectile", "ranged", "thrown"} else "melee"
    trajectory: Mapping[str, Any] | None = action.trajectory

    if channel == "melee":
        actor_position = copy.deepcopy(dict(positions[actor_ref]))
        target_position = copy.deepcopy(dict(positions[target_ref]))
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
            duration_ms=max(1, int(action.release_at_ms) - int(action.start_at_ms)),
        )
        capability = exact._qi_enhanced_capability(capability, qi_preview)
        params = dict(profile.effect_parameters)
        params["intended_target_ref"] = target_ref
        profile = exact.ActionProfile(**{**profile.__dict__, "effect_parameters": params})
        moved, approach = exact.close_attacker_into_reach(
            attacker_ref=actor_ref,
            defender_ref=target_ref,
            positions=positions,
            attacker_position=exact._pos(actor_position),
            defender_position=exact._pos(target_position),
            attacker_capability=capability,
            profile=profile,
            body_refs=body_refs,
            obstacles=combat.get("obstacles", []),
        )
        if isinstance(approach, Mapping) and approach.get("moved"):
            positions[actor_ref] = moved.to_record()
            positions[actor_ref]["elevation_mm"] = int(actor_position.get("elevation_mm", 0))
        reach = exact.physical_reach_mm(profile)
        if reach > 0 and exact.planar_distance_mm(positions[actor_ref], positions[target_ref]) > reach:
            return None
        trajectory = {
            "launch_x_mm": int(positions[actor_ref]["x_mm"]),
            "launch_y_mm": int(positions[actor_ref]["y_mm"]),
            "launch_elevation_mm": int(positions[actor_ref].get("elevation_mm", 0)),
            "aim_x_mm": int(positions[target_ref]["x_mm"]),
            "aim_y_mm": int(positions[target_ref]["y_mm"]),
            "aim_elevation_mm": int(positions[target_ref].get("elevation_mm", 0)),
        }

    geometry = profile.effect_parameters.get("geometry")
    trace = exact.trace_attack_geometry(
        positions,
        actor_ref=actor_ref,
        aim_ref=target_ref,
        body_refs=body_refs,
        geometry=geometry,
        obstacles=combat.get("obstacles", []),
        target_limit=1,
        maximum_range_m=(
            profile.effect_parameters.get("maximum_range_m")
            if channel == "projectile"
            else profile.effect_parameters.get("physical_reach_m")
        ),
        channel=channel,
        trajectory=trajectory,
    )
    if not isinstance(trace, Mapping):
        raise ValueError("autonomous_attack_geometry_trace_invalid")
    contacts = trace.get("contacts", [])
    if not isinstance(contacts, list):
        raise ValueError("autonomous_attack_geometry_contacts_invalid")
    if not contacts:
        return None
    if not isinstance(contacts[0], Mapping):
        raise ValueError("autonomous_attack_first_contact_invalid")
    first_ref = contacts[0].get("participant_ref")
    if not isinstance(first_ref, str) or not first_ref:
        raise ValueError("autonomous_attack_first_contact_unresolved")
    if first_ref == target_ref:
        return None
    if exact._side_of(combat, first_ref) == exact._side_of(combat, actor_ref):
        return first_ref
    return None


def install() -> None:
    """Install the autonomous declaration-time safety gate once per process."""
    global _INSTALLED
    if _INSTALLED:
        return
    from . import exact_combat as exact

    original = exact._schedule_action
    if getattr(original, "_friendly_line_safety", False):
        _INSTALLED = True
        return

    def schedule_with_friendly_line_safety(**kwargs: Any) -> Any:
        if str(kwargs.get("decision_origin") or "") != "player":
            combat = kwargs.get("combat")
            if not isinstance(combat, Mapping):
                raise ValueError("autonomous_attack_control_state_invalid")
            positions = combat.get("positions", {})
            combatants = combat.get("combatants", {})
            actor_ref = str(kwargs.get("actor_ref") or "")
            target_ref = str(kwargs.get("target_ref") or "")
            people = kwargs.get("people")
            if not isinstance(positions, Mapping) or not isinstance(combatants, Mapping) or not isinstance(people, Mapping):
                raise ValueError("autonomous_attack_control_state_invalid")
            if actor_ref not in positions or target_ref not in positions:
                raise ValueError("autonomous_attack_position_unresolved")
            if not isinstance(combatants.get(actor_ref), Mapping) or actor_ref not in people:
                raise ValueError("autonomous_attack_actor_unresolved")
        action = original(**kwargs)
        blocker = _prospective_friendly_blocker(
            exact,
            combat=kwargs["combat"],
            action=action,
            people=kwargs["people"],
            equipment_ledger=kwargs["equipment_ledger"],
        )
        if blocker is not None:
            raise ValueError("friendly_attack_lane_blocked")
        return action

    schedule_with_friendly_line_safety._friendly_line_safety = True  # type: ignore[attr-defined]
    exact._schedule_action = schedule_with_friendly_line_safety

    original_resolve = exact._resolve_scheduled_action
    if not getattr(original_resolve, "_friendly_line_safety", False):
        def resolve_with_dynamic_friendly_line_safety(**kwargs: Any) -> Any:
            action = kwargs.get("action")
            combat = kwargs.get("combat")
            people = kwargs.get("people")
            equipment_ledger = kwargs.get("equipment_ledger")
            if (
                action is not None
                and str(getattr(action, "decision_origin", "")) != "player"
                and getattr(getattr(action, "profile", None), "delivery", "") in {"projectile", "ranged", "thrown"}
                and isinstance(combat, Mapping)
                and isinstance(people, Mapping)
                and isinstance(equipment_ledger, Mapping)
            ):
                blocker = _prospective_friendly_blocker(
                    exact, combat=combat, action=action, people=people, equipment_ledger=equipment_ledger,
                )
                if blocker is not None:
                    return {
                        "actor_ref": str(action.actor_ref),
                        "intended_ref": str(action.target_ref),
                        "action_kind": str(action.action_kind),
                        "weapon_ref": str(action.weapon_ref),
                        "poison_ref": action.poison_ref,
                        "hit_zone": str(action.hit_zone),
                        "target_structure_ref": action.target_structure_ref,
                        "decision_origin": str(action.decision_origin),
                        "declared_at_ms": int(action.declared_at_ms),
                        "start_at_ms": int(action.start_at_ms),
                        "ready_delay_ms": int(action.ready_delay_ms),
                        "previous_ready_weapon_ref": action.previous_ready_weapon_ref,
                        "commit_at_ms": int(action.commit_at_ms),
                        "release_at_ms": int(action.release_at_ms),
                        "contact_at_ms": int(action.contact_at_ms),
                        "recovery_end_ms": int(action.recovery_end_ms),
                        "result": "autonomous_attack_withheld_friendly_lane",
                        "friendly_blocker_ref": blocker,
                        "safety_phase": "pre_resolution_dynamic_lane_recheck",
                    }
            return original_resolve(**kwargs)

        resolve_with_dynamic_friendly_line_safety._friendly_line_safety = True  # type: ignore[attr-defined]
        exact._resolve_scheduled_action = resolve_with_dynamic_friendly_line_safety
    _INSTALLED = True


__all__ = ["install"]
