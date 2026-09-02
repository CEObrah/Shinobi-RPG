"""Deterministic physical-response layer for exact personal combat.

This module does not decide damage. It converts perception, body state,
geometry, equipment-derived options and current commitment into one lawful
physical response. Movement responses update real local coordinates; non-
movement responses update directional body/weapon commitment that the resolver
can carry across processing boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from .geometry import (
    DEFAULT_BODY_RADIUS_MM,
    angular_difference_mdeg,
    facing_to_target_mdeg,
    path_clear,
    planar_distance_mm,
    trace_attack_geometry,
)
from .models import ActionProfile, CapabilityProfile, Participant, PositionState

DEFENSE_RESPONSES = (
    "evade", "reposition", "parry", "deflect", "block", "brace", "counter_intercept",
)
_MOVEMENT_LOCKS = frozenset({"immobilized", "restrained", "grappled", "entangled", "confined", "pinned"})
_ACTION_LOCKS = frozenset({"stunned", "unconscious", "incapacitated"})
_HARD_RESTRAINTS = frozenset({"restrained", "entangled", "pinned"})
_POSTURE_IMPAIRMENTS = frozenset({"knockdown", "knocked_down", "prone"})
_VISION_IMPAIRMENTS = frozenset({"blind", "blinded", "blind_both_eyes"})


@dataclass(frozen=True)
class PhysicalDefenseDecision:
    detected: bool
    detection_margin: int
    response: str
    before_position: PositionState
    after_position: PositionState
    displacement_mm: int
    reaction_delay_ms: int
    recovery_ms: int
    defense_factor_milli: int
    reaction_availability_milli: int
    balance_after_milli: int
    limb_commitment_after_milli: int
    weapon_position_after: str
    attack_angle_mdeg: int
    tracking_milli: int
    force_transmission_milli: int
    control_disruption: int
    displacement_resistance_milli: int
    interrupts_attacker: bool
    contact_surface: str
    reason: str

    def trace(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "detection_margin": self.detection_margin,
            "response": self.response,
            "before": self.before_position.to_record(),
            "after": self.after_position.to_record(),
            "displacement_mm": self.displacement_mm,
            "reaction_delay_ms": self.reaction_delay_ms,
            "recovery_ms": self.recovery_ms,
            "defense_factor_milli": self.defense_factor_milli,
            "reaction_availability_milli": self.reaction_availability_milli,
            "balance_after_milli": self.balance_after_milli,
            "limb_commitment_after_milli": self.limb_commitment_after_milli,
            "weapon_position_after": self.weapon_position_after,
            "attack_angle_mdeg": self.attack_angle_mdeg,
            "tracking_milli": self.tracking_milli,
            "force_transmission_milli": self.force_transmission_milli,
            "control_disruption": self.control_disruption,
            "displacement_resistance_milli": self.displacement_resistance_milli,
            "interrupts_attacker": self.interrupts_attacker,
            "contact_surface": self.contact_surface,
            "reason": self.reason,
        }


def movement_speed_mmps(capability: CapabilityProfile) -> int:
    """Local combat movement speed; capability is uncapped, hardware is not."""
    # 100 mobility ~= 4 m/s combat footwork; 200 ~= 6.5 m/s. Values above that
    # remain useful without creating teleportation.
    return max(700, 1500 + max(0, int(capability.mobility)) * 25)


def physical_reach_mm(profile: ActionProfile | None) -> int:
    if profile is None or not profile.external_contact:
        return 0
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    raw = params.get("physical_reach_m", params.get("maximum_range_m"))
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return max(100, int(round(float(raw) * 1000)))
    if profile.delivery in {"projectile", "thrown", "ranged"}:
        return 0
    # Ordinary unarmed/body contact. Kicks are handled inside this compact body
    # envelope rather than inheriting the removed abstract short-range band.
    return 1100


def status_action_allowed(status_families: Sequence[str], action: str) -> bool:
    statuses = set(status_families)
    if statuses & _ACTION_LOCKS:
        return action in {"hold"}
    # A fully restrained/entangled body cannot simply execute the same attack
    # or movement vocabulary as an unrestricted fighter. Breaking that state
    # requires a registered release/escape mechanic rather than pretending it
    # is only a defense-number penalty.
    if statuses & _HARD_RESTRAINTS and action in {"attack", "capture", "escape", "extract", "disengage"}:
        return False
    if action in {"escape", "extract", "disengage"} and statuses & _MOVEMENT_LOCKS:
        return False
    return True


def _attack_warning_ms(profile: ActionProfile | None, *, distance_mm: int) -> int:
    if profile is None:
        return 150
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    startup = max(0, int(profile.startup_ms))
    projectile = params.get("projectile")
    flight = projectile.get("flight_time_ms", 0) if isinstance(projectile, Mapping) else 0
    if not isinstance(flight, int) or isinstance(flight, bool):
        flight = 0
    # Physical approach time is already authored into the action when closing is
    # required. It provides warning only if the defender can perceive the approach.
    approach = params.get("approach_time_ms", 0)
    if not isinstance(approach, int) or isinstance(approach, bool):
        approach = 0
    return max(40, startup + max(0, flight) + max(0, approach))


def detect_attack(
    *,
    attacker: Participant,
    defender: Participant,
    attacker_position: PositionState,
    defender_position: PositionState,
    attacker_capability: CapabilityProfile,
    defender_capability: CapabilityProfile,
    profile: ActionProfile | None,
    line_of_sight: bool,
) -> tuple[bool, int, int, int]:
    """Return detected, margin, reaction_delay_ms, incoming_angle_mdeg."""
    bearing = facing_to_target_mdeg(defender_position.to_record(), attacker_position.to_record())
    facing_delta = angular_difference_mdeg(defender_position.facing_mdeg, bearing)
    signed_delta = (bearing - defender_position.facing_mdeg + 540_000) % 360_000 - 180_000
    facing_penalty = 0 if facing_delta <= 60_000 else (20 if facing_delta <= 120_000 else 45)
    statuses = set(defender.status_families)
    blind_both = bool(statuses & _VISION_IMPAIRMENTS)
    sensory_penalty = 120 if blind_both else 0
    # Positive relative bearing is the defender's left hemisphere; negative is
    # the right. A destroyed eye therefore matters directionally instead of
    # becoming a generic global defense debuff.
    if "blind_left_eye" in statuses and signed_delta > 30_000:
        sensory_penalty += 35
    if "blind_right_eye" in statuses and signed_delta < -30_000:
        sensory_penalty += 35
    observed = attacker.participant_ref in defender.information.observed_refs
    observation_bonus = 20 if blind_both and observed else (45 if observed else 0)
    los_bonus = 0 if blind_both else (30 if line_of_sight else -45)
    distance_mm=planar_distance_mm(attacker_position.to_record(), defender_position.to_record())
    warning = _attack_warning_ms(profile, distance_mm=distance_mm)
    speed = max(1, int(profile.speed_score if profile is not None else attacker_capability.mobility))
    speed_pressure = min(120, speed // 4)
    surprise = max(0, int(attacker.information.surprise_milli) // 25)
    projectile_penalty=0
    if profile is not None and profile.delivery in {"projectile","ranged","thrown"}:
        params=profile.effect_parameters if isinstance(profile.effect_parameters,Mapping) else {}
        visibility=max(50,min(1000,int(params.get("projectile_visibility_milli",700))))
        width=max(1,int(params.get("projectile_width_mm",8)))
        release_concealment=max(0,min(1000,int(params.get("release_concealment_milli",0))))
        distance_m=max(0.0,distance_mm/1000.0)
        projectile_penalty=(1000-visibility)//4 + max(0,10-width)*6 + int(distance_m*max(0,700-visibility)/120) + release_concealment//10
    margin = (
        int(defender_capability.perception) * 2
        + int(defender_capability.reaction)
        + observation_bonus + los_bonus
        + min(80, warning // 10)
        - facing_penalty - sensory_penalty - speed_pressure - surprise - projectile_penalty
    )
    detected = margin >= 0 and not (statuses & _ACTION_LOCKS)
    reaction_score = max(1, int(defender_capability.reaction) + int(defender_capability.perception) // 2)
    delay = max(35, 115_000 // (reaction_score + 80))
    return detected, margin, delay, bearing


def _defense_displacement_window_ms(profile: ActionProfile | None) -> int:
    """Return movement time still available after the attacker has closed.

    Melee ``approach_time_ms`` is already consumed by the attacker's tracked
    closing movement. It remains valid warning for detection/orientation, but
    counting it again here would let a defender spend the same seconds twice: once
    while being tracked by the approach and again as a full post-close dodge.
    Projectiles still grant their real post-release flight time.
    """
    if profile is None:
        return 150
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    startup = max(0, int(profile.startup_ms))
    if profile.delivery in {"projectile", "ranged", "thrown"}:
        projectile = params.get("projectile")
        flight = projectile.get("flight_time_ms", 0) if isinstance(projectile, Mapping) else 0
        if isinstance(flight, bool) or not isinstance(flight, int):
            flight = 0
        return max(40, startup + max(0, flight))
    return max(40, startup)


def _preference_bonus(participant: Participant, response: str) -> int:
    prefs = participant.physical_defense_preferences
    if response not in prefs:
        return 0
    return max(0, 80 - prefs.index(response) * 12)


def _candidate_displacements(
    position: PositionState,
    *, incoming_bearing_mdeg: int,
    max_displacement_mm: int,
) -> tuple[tuple[str, int, int, int], ...]:
    # Sidesteps first, then diagonals/backstep, then inside movement. Labels are
    # retained for traceability but all are just coordinate changes.
    offsets = (
        ("sidestep_left", incoming_bearing_mdeg + 90_000),
        ("sidestep_right", incoming_bearing_mdeg - 90_000),
        ("diagonal_left", incoming_bearing_mdeg + 135_000),
        ("diagonal_right", incoming_bearing_mdeg - 135_000),
        ("backstep", incoming_bearing_mdeg + 180_000),
        ("move_inside", incoming_bearing_mdeg),
    )
    rows = []
    for label, angle in offsets:
        normalized = angle % 360_000
        rad = math.radians(normalized / 1000.0)
        rows.append((
            label,
            int(position.x_mm + round(math.cos(rad) * max_displacement_mm)),
            int(position.y_mm + round(math.sin(rad) * max_displacement_mm)),
            normalized,
        ))
    return tuple(rows)




def _turn_toward_mdeg(current: int, target: int, *, max_turn_mdeg: int) -> int:
    current %= 360_000; target %= 360_000
    delta = (target - current + 540_000) % 360_000 - 180_000
    if abs(delta) <= max_turn_mdeg:
        return target
    return (current + (max_turn_mdeg if delta > 0 else -max_turn_mdeg)) % 360_000

def _position_with(position: PositionState, *, x_mm: int, y_mm: int, facing_mdeg: int, vx_mmps: int, vy_mmps: int, stance: str) -> PositionState:
    return PositionState(
        zone_ref=position.zone_ref,
        elevation_mm=position.elevation_mm,
        cover_milli=position.cover_milli,
        x_mm=x_mm,
        y_mm=y_mm,
        facing_mdeg=facing_mdeg % 360_000,
        body_radius_mm=position.body_radius_mm,
        vx_mmps=vx_mmps,
        vy_mmps=vy_mmps,
        stance=stance,
    )


def _movement_response(
    *,
    response: str,
    attacker_ref: str,
    defender_ref: str,
    participant_positions: Mapping[str, Mapping[str, Any]],
    defender_position: PositionState,
    incoming_bearing_mdeg: int,
    defender_capability: CapabilityProfile,
    reaction_delay_ms: int,
    warning_ms: int,
    body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]],
) -> PositionState | None:
    available_ms = max(0, warning_ms - reaction_delay_ms)
    if available_ms <= 0:
        return None
    speed = movement_speed_mmps(defender_capability)
    displacement = min(3200, max(350, speed * available_ms // 1000))
    current = dict(participant_positions)
    current[defender_ref] = defender_position.to_record()
    candidates = _candidate_displacements(defender_position, incoming_bearing_mdeg=incoming_bearing_mdeg, max_displacement_mm=displacement)
    # Reposition prefers angle/spacing; evade prefers quickest lateral exit.
    if response == "reposition":
        candidates = tuple(sorted(candidates, key=lambda row: (0 if row[0].startswith("diagonal") else 1, row[0])))
    velocity_angle: int | None = None
    if abs(defender_position.vx_mmps) + abs(defender_position.vy_mmps) >= 300:
        velocity_angle = int(round(math.degrees(math.atan2(defender_position.vy_mmps, defender_position.vx_mmps)) * 1000)) % 360000
    for _label, x, y, facing in candidates:
        if velocity_angle is not None:
            turn = angular_difference_mdeg(velocity_angle, facing)
            # Momentum does not forbid redirection, but a near-instant complete
            # reversal is not a free dodge. More warning time can pay the turn.
            if turn >= 135000 and available_ms < 300:
                continue
            if turn >= 90000 and available_ms < 180:
                continue
        if not path_clear(current, actor_ref=defender_ref, end_x_mm=x, end_y_mm=y, body_refs=body_refs, obstacles=obstacles):
            continue
        dx, dy = x - defender_position.x_mm, y - defender_position.y_mm
        return _position_with(
            defender_position, x_mm=x, y_mm=y, facing_mdeg=facing,
            vx_mmps=dx * 1000 // max(1, available_ms), vy_mmps=dy * 1000 // max(1, available_ms),
            stance="moving_defense",
        )
    return None


def select_physical_defense(
    *,
    attacker: Participant,
    defender: Participant,
    attacker_position: PositionState,
    defender_position: PositionState,
    attacker_capability: CapabilityProfile,
    defender_capability: CapabilityProfile,
    profile: ActionProfile | None,
    line_of_sight: bool,
    participant_positions: Mapping[str, Mapping[str, Any]],
    body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]] = (),
    at_ms: int = 0,
) -> PhysicalDefenseDecision:
    detected, detection_margin, reaction_delay, attack_angle = detect_attack(
        attacker=attacker, defender=defender,
        attacker_position=attacker_position, defender_position=defender_position,
        attacker_capability=attacker_capability, defender_capability=defender_capability,
        profile=profile, line_of_sight=line_of_sight,
    )
    warning = _attack_warning_ms(profile, distance_mm=planar_distance_mm(attacker_position.to_record(), defender_position.to_record()))
    movement_warning = _defense_displacement_window_ms(profile)
    active_load=max(0,min(1000,int(defender.active_defense_load_milli)))
    reaction_availability=max(60,1000-active_load)
    # Shared commitment is not just a trace number: a saturated defender reacts
    # later to the next threat. This is what prevents a fresh full dodge/parry
    # against every attack in a rapid multi-attacker or projectile sequence.
    reaction_delay=max(reaction_delay,reaction_delay*(2000-reaction_availability)//1000)
    statuses = set(defender.status_families)
    posture_impaired = bool(statuses & _POSTURE_IMPAIRMENTS)
    recovery_lock = max(0, int(defender.recovery_remaining_ms))
    movement_locked = bool(statuses & _MOVEMENT_LOCKS) or recovery_lock > warning
    options: list[tuple[int, str, PositionState | None]] = []

    if not detected:
        return PhysicalDefenseDecision(
            detected=False, detection_margin=detection_margin, response="none",
            before_position=defender_position, after_position=defender_position,
            displacement_mm=0, reaction_delay_ms=reaction_delay, recovery_ms=0,
            defense_factor_milli=max(80, 350 * reaction_availability // 1000),
            reaction_availability_milli=reaction_availability,
            balance_after_milli=defender.balance_milli,
            limb_commitment_after_milli=defender.limb_commitment_milli,
            weapon_position_after=defender.weapon_position,
            attack_angle_mdeg=attack_angle, tracking_milli=1000,
            force_transmission_milli=1000, control_disruption=0, displacement_resistance_milli=0,
            interrupts_attacker=False, contact_surface="none",
            reason="attack_not_detected_in_time",
        )

    if not movement_locked:
        for response in ("evade", "reposition"):
            moved = _movement_response(
                response=response, attacker_ref=attacker.participant_ref, defender_ref=defender.participant_ref,
                participant_positions=participant_positions, defender_position=defender_position,
                incoming_bearing_mdeg=attack_angle, defender_capability=defender_capability,
                reaction_delay_ms=reaction_delay, warning_ms=movement_warning, body_refs=body_refs, obstacles=obstacles,
            )
            if moved is not None:
                mobility_axis = int(defender_capability.mobility) * 4 + int(defender_capability.reaction) * 3 + int(defender_capability.perception) * 2
                mobility_axis = mobility_axis * max(250, int(defender.balance_milli)) // 1000
                if response == "reposition":
                    mobility_axis += int(defender_capability.control)
                if posture_impaired:
                    mobility_axis = mobility_axis * 55 // 100
                options.append(((mobility_axis + _preference_bonus(defender, response)) * reaction_availability // 1000, response, moved))

    prefs = set(defender.physical_defense_preferences)
    # Equipment availability is projected as allowed preferences. If no explicit
    # preference projection exists, ordinary weapon users may still parry when
    # their current action profile represents a held direct-contact method.
    current_profile = defender.action_profile
    has_weapon_like = bool(current_profile is not None and current_profile.external_contact and current_profile.method_ref and current_profile.delivery not in {"projectile", "ranged"})
    can_parry = "parry" in prefs or has_weapon_like
    can_block = "block" in prefs or any(d.defense_kind in {"weapon_guard", "physical_guard", "prepared_directional_protection"} for d in defender.reactive_defenses)
    if can_parry:
        score = int(defender_capability.defense) * 4 + int(defender_capability.control) * 3 + int(defender_capability.reaction) * 2
        if defender.weapon_position not in {"guard", "ready", "committed_guard"}:
            score -= 180 + int(defender.limb_commitment_milli) // 4
        score = score * max(300, int(defender.balance_milli)) // 1000
        options.append(((score + _preference_bonus(defender, "parry")) * reaction_availability // 1000, "parry", None))
        options.append(((score - 20 + _preference_bonus(defender, "deflect")) * reaction_availability // 1000, "deflect", None))
    if can_block:
        score = int(defender_capability.defense) * 4 + int(defender_capability.control) * 3 + int(defender_capability.reaction) * 2
        if defender.weapon_position in {"extended_attack", "extended_parry", "displaced_guard"}:
            score -= int(defender.limb_commitment_milli) // 3
        score = score * max(350, int(defender.balance_milli)) // 1000
        options.append(((score + _preference_bonus(defender, "block")) * reaction_availability // 1000, "block", None))
    brace_score = int(defender_capability.defense) * 3 + int(defender_capability.control) * 2 + int(defender.readiness) * 2
    options.append(((brace_score + _preference_bonus(defender, "brace")) * max(400,reaction_availability) // 1000, "brace", None))
    if not movement_locked and reaction_availability>=350 and defender.intent.action in {"attack", "capture"}:
        # Counter-intercept is only a response option when the defender was
        # already prepared to act and the attacker is physically close enough.
        d = planar_distance_mm(attacker_position.to_record(), defender_position.to_record())
        own_reach = physical_reach_mm(defender.action_profile)
        if own_reach > 0 and d <= own_reach:
            score = int(defender_capability.offense) * 3 + int(defender_capability.reaction) * 3 + int(defender_capability.control) * 2
            posture_bias={"rare":-220,"selective":0,"active":220}.get(defender.counterattack_posture,0)
            options.append(((score + posture_bias + _preference_bonus(defender, "counter_intercept")) * reaction_availability // 1000, "counter_intercept", None))

    if not options:
        response, moved = "brace", None
    else:
        _score, response, moved = max(options, key=lambda row: (row[0], -DEFENSE_RESPONSES.index(row[1])))

    after = moved if moved is not None else defender_position
    if moved is None and response in {"parry", "deflect", "block", "brace", "counter_intercept"}:
        # A stationary defense still has to turn the body/guard toward the
        # threat. Reaction determines how much rotation can occur inside the
        # available warning window; rear attacks therefore cannot be answered
        # as though the defender were already square to them.
        available_ms = max(0, warning - reaction_delay)
        turn_rate_mdeg_per_s = 150_000 + max(0, int(defender_capability.reaction)) * 700
        max_turn = min(180_000, turn_rate_mdeg_per_s * available_ms // 1000)
        new_facing = _turn_toward_mdeg(defender_position.facing_mdeg, attack_angle, max_turn_mdeg=max_turn)
        after = _position_with(
            defender_position, x_mm=defender_position.x_mm, y_mm=defender_position.y_mm,
            facing_mdeg=new_facing, vx_mmps=defender_position.vx_mmps, vy_mmps=defender_position.vy_mmps,
            stance="guarding" if response != "brace" else "braced",
        )
    displacement = planar_distance_mm(defender_position.to_record(), after.to_record())
    load = active_load
    base_factor = {
        "evade": 900, "reposition": 820, "parry": 850, "deflect": 800,
        "block": 760, "brace": 520, "counter_intercept": 700,
    }.get(response, 350)
    defense_factor = max(60, base_factor * reaction_availability // 1000)
    if posture_impaired:
        defense_factor = max(60, defense_factor * 70 // 100)
    balance_after = {
        "evade": 690, "reposition": 760, "parry": 820, "deflect": 800,
        "block": 900, "brace": 970, "counter_intercept": 720,
    }.get(response, defender.balance_milli)
    limb_after = {
        "evade": 180, "reposition": 140, "parry": 620, "deflect": 540,
        "block": 500, "brace": 320, "counter_intercept": 760,
    }.get(response, 0)
    recovery = max(90, reaction_delay + {"evade":180,"reposition":140,"parry":220,"deflect":180,"block":260,"brace":220,"counter_intercept":300}.get(response,160))
    weapon_after = {
        "parry":"extended_parry", "deflect":"displaced_guard", "block":"committed_guard",
        "counter_intercept":"extended_attack", "brace":"guarded_brace",
    }.get(response, defender.weapon_position)
    tracking = max(100, min(1000, (int(attacker_capability.reaction) * 3 + int(attacker_capability.control) * 2 + max(0, warning - reaction_delay) // 2)))
    base_force_transmission = {
        "evade": 900, "reposition": 900, "parry": 280, "deflect": 420,
        "block": 520, "brace": 780, "counter_intercept": 600,
    }.get(response, 1000)
    # As reaction availability collapses, a late parry/block may still contact
    # the attack but cannot magically retain full mitigation quality.
    force_transmission = 1000 - (1000-base_force_transmission) * reaction_availability // 1000
    control_disruption = {
        "parry": 65, "deflect": 85, "block": 35, "brace": 5, "counter_intercept": 90,
        "evade": 20, "reposition": 30,
    }.get(response, 0)
    displacement_resistance = {
        "brace": 900, "block": 720, "parry": 350, "deflect": 250,
        "counter_intercept": 300, "evade": 100, "reposition": 150,
    }.get(response, 0)
    interrupts_attacker = bool(
        response == "counter_intercept"
        and reaction_availability >= 450
        and reaction_delay + max(60, int(defender.recovery_remaining_ms) // 4) < warning
        and defender_capability.reaction + defender_capability.control >= attacker_capability.reaction + attacker_capability.control // 2
    )
    contact_surface = {
        "parry": "weapon", "deflect": "weapon", "block": "weapon_or_body_guard",
        "brace": "body_or_guard", "counter_intercept": "intercepting_weapon_or_limb",
        "evade": "none", "reposition": "none",
    }.get(response, "none")
    return PhysicalDefenseDecision(
        detected=True, detection_margin=detection_margin, response=response,
        before_position=defender_position, after_position=after,
        displacement_mm=displacement, reaction_delay_ms=reaction_delay, recovery_ms=recovery,
        defense_factor_milli=defense_factor, reaction_availability_milli=reaction_availability, balance_after_milli=balance_after,
        limb_commitment_after_milli=limb_after, weapon_position_after=weapon_after,
        attack_angle_mdeg=attack_angle, tracking_milli=tracking,
        force_transmission_milli=force_transmission, control_disruption=control_disruption,
        displacement_resistance_milli=displacement_resistance, interrupts_attacker=interrupts_attacker,
        contact_surface=contact_surface,
        reason="lawful_physical_response_selected",
    )



def close_attacker_into_reach(
    *,
    attacker_ref: str,
    defender_ref: str,
    positions: Mapping[str, Mapping[str, Any]],
    attacker_position: PositionState,
    defender_position: PositionState,
    attacker_capability: CapabilityProfile,
    profile: ActionProfile | None,
    body_refs: Sequence[str],
    obstacles: Sequence[Mapping[str, Any]] = (),
) -> tuple[PositionState, dict[str, Any]]:
    """Physically close a melee attacker only as far as the authored action allows.

    The command layer records how much approach travel was available when the
    action began. If the target moved farther during startup, this function may
    fail to reach rather than teleporting the strike.
    """
    if profile is None or not profile.external_contact or profile.delivery in {"projectile", "ranged", "thrown"}:
        return attacker_position, {"moved": False, "reason": "no_melee_approach"}
    reach = physical_reach_mm(profile)
    if reach <= 0:
        return attacker_position, {"moved": False, "reason": "no_physical_reach"}
    start = attacker_position.to_record(); target = defender_position.to_record()
    d = planar_distance_mm(start, target)
    required = max(0, d - reach)
    if required <= 0:
        return attacker_position, {"moved": False, "reason": "already_in_reach", "required_mm": 0}
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    allowed = params.get("approach_distance_mm", required)
    if isinstance(allowed, bool) or not isinstance(allowed, int):
        allowed = required
    allowed = max(0, allowed)
    if allowed <= 0:
        return attacker_position, {"moved": False, "reason": "target_moved_beyond_committed_approach", "required_mm": required, "allowed_mm": allowed}
    dx = defender_position.x_mm - attacker_position.x_mm
    dy = defender_position.y_mm - attacker_position.y_mm
    plen = max(1, math.isqrt(dx*dx + dy*dy))
    move = min(required, allowed)
    ex = attacker_position.x_mm + dx * move // plen
    ey = attacker_position.y_mm + dy * move // plen
    current = dict(positions); current[attacker_ref] = attacker_position.to_record(); current[defender_ref] = defender_position.to_record()
    if not path_clear(current, actor_ref=attacker_ref, end_x_mm=ex, end_y_mm=ey, body_refs=body_refs, obstacles=obstacles):
        return attacker_position, {"moved": False, "reason": "approach_lane_blocked", "required_mm": required}
    facing = facing_to_target_mdeg(attacker_position.to_record(), defender_position.to_record())
    approach_ms = params.get("approach_time_ms", 0)
    if isinstance(approach_ms, bool) or not isinstance(approach_ms, int) or approach_ms <= 0:
        approach_ms = max(1, move * 1000 // max(1, movement_speed_mmps(attacker_capability)))
    moved = _position_with(
        attacker_position, x_mm=ex, y_mm=ey, facing_mdeg=facing,
        vx_mmps=(ex-attacker_position.x_mm)*1000//max(1,approach_ms),
        vy_mmps=(ey-attacker_position.y_mm)*1000//max(1,approach_ms),
        stance="approaching",
    )
    reason = "closed_into_melee_reach" if required <= allowed else "partial_committed_approach"
    result = {"moved": True, "reason": reason, "distance_mm": move, "approach_time_ms": approach_ms}
    if required > allowed:
        result["required_mm"] = required
        result["allowed_mm"] = allowed
        result["remaining_mm"] = required - allowed
    return moved, result

def contact_after_defense(
    *,
    attacker_ref: str,
    defender_ref: str,
    positions: Mapping[str, Mapping[str, Any]],
    profile: ActionProfile | None,
    obstacles: Sequence[Mapping[str, Any]],
    trajectory: Mapping[str, Any] | None,
    tracking_milli: int,
    original_defender_position: PositionState,
    body_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Recalculate physical contact after the defender's response."""
    if profile is None or not profile.external_contact:
        return {"contact": True, "reason": "nonphysical_or_unspecified"}
    actor = positions.get(attacker_ref); target = positions.get(defender_ref)
    if not isinstance(actor, Mapping) or not isinstance(target, Mapping):
        return {"contact": False, "reason": "missing_position"}
    params = profile.effect_parameters if isinstance(profile.effect_parameters, Mapping) else {}
    delivery = str(profile.delivery or "direct")
    if delivery in {"projectile", "ranged", "thrown"}:
        geometry = params.get("geometry") if isinstance(params.get("geometry"), Mapping) else {"shape":"direct","width_m":0.15}
        trace = trace_attack_geometry(
            positions, actor_ref=attacker_ref, aim_ref=defender_ref,
            body_refs=tuple(body_refs) or (defender_ref,), geometry=geometry,
            obstacles=obstacles, target_limit=1, maximum_range_m=params.get("maximum_range_m"), channel="projectile", trajectory=trajectory,
        )
        contacts = trace.get("contacts") if isinstance(trace, Mapping) else None
        contacted_ref = contacts[0].get("participant_ref") if isinstance(contacts, list) and contacts and isinstance(contacts[0], Mapping) else None
        return {"contact": bool(contacts), "contacted_ref": contacted_ref, "intended_ref": defender_ref, "reason": "projectile_trajectory_intersection", "trace": trace}

    reach = physical_reach_mm(profile)
    distance = planar_distance_mm(actor, target)
    if reach > 0 and distance > reach:
        return {"contact": False, "reason": "moved_out_of_reach", "distance_mm": distance, "reach_mm": reach}
    committed_trajectory = params.get("committed_melee_trajectory") if isinstance(params.get("committed_melee_trajectory"), Mapping) else None
    committed_displacement = 0
    if isinstance(committed_trajectory, Mapping):
        committed_aim = dict(target)
        try:
            committed_aim["x_mm"] = int(committed_trajectory["aim_x_mm"])
            committed_aim["y_mm"] = int(committed_trajectory["aim_y_mm"])
            committed_aim["elevation_mm"] = int(committed_trajectory.get("aim_elevation_mm", target.get("elevation_mm", 0)))
            committed_displacement = planar_distance_mm(committed_aim, target)
        except (KeyError, TypeError, ValueError):
            committed_trajectory = None
            committed_displacement = 0

    # A low-tracking committed attack keeps the line it physically began on.
    # Better tracking may redirect toward the target's current position during
    # remaining startup, but target identity never itself guarantees contact.
    use_committed_lane = bool(committed_trajectory) and tracking_milli < 650
    intended_ref = params.get("intended_target_ref")
    if not isinstance(intended_ref, str) or intended_ref not in positions:
        intended_ref = defender_ref
    blocker = trace_attack_geometry(
        positions, actor_ref=attacker_ref, aim_ref=intended_ref,
        body_refs=tuple(body_refs) or (defender_ref,),
        geometry={"shape":"direct","width_m":0.35,"length_m":max(0.2, reach/1000.0)},
        obstacles=obstacles, target_limit=1, maximum_range_m=max(0.2, reach/1000.0), channel="melee",
        trajectory=committed_trajectory if use_committed_lane else None,
    )
    moved = planar_distance_mm(original_defender_position.to_record(), target)
    contacts = blocker.get("contacts") if isinstance(blocker, Mapping) else None
    contacted_ref = contacts[0].get("participant_ref") if isinstance(contacts, list) and contacts and isinstance(contacts[0], Mapping) else None
    return {
        "contact": bool(contacts), "contacted_ref": contacted_ref, "intended_ref": intended_ref,
        "reason": "committed_melee_lane_intersection" if use_committed_lane else "tracked_melee_swept_path_intersection",
        "distance_mm": distance, "reach_mm": reach, "trace": blocker,
        "tracking_milli": tracking_milli, "defense_displacement_mm": moved,
        "committed_target_displacement_mm": committed_displacement,
    }


__all__ = [
    "DEFENSE_RESPONSES", "PhysicalDefenseDecision", "close_attacker_into_reach", "contact_after_defense",
    "detect_attack", "movement_speed_mmps", "physical_reach_mm",
    "select_physical_defense", "status_action_allowed",
]
